"""The consumer: polls SQS, runs one rollout through Harbor, records it.

Deliberately a loop and not a framework. One message at a time, so concurrency
is replicas rather than threads: the number of rollouts in flight is the number
of copies of this process.

Cannot enqueue submissions. That belongs to the API, and the IAM policy
enforces the split.
"""

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import boto3

from common import results
from worker import trajectory
from worker.gateway import delete_key, mint_key, usage_for_key

QUEUE_URL = os.environ["QUEUE_URL"]
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000/v1")
TASKS_DIR = Path(os.environ.get("TASKS_DIR", "tasks")).resolve()
# Kept rather than thrown away with the temp dir: `harbor view` reads these,
# which is how a failed graded run gets inspected.
JOBS_DIR = Path(os.environ.get("JOBS_DIR", "jobs")).resolve()
MODEL = os.environ.get("MODEL", "gemma")
RATE_LIMIT_RPM = int(os.environ.get("RATE_LIMIT_RPM", "60"))
# Every graded rollout is one directory here, and `harbor view` is pointed at
# this directory. Its own subdirectory rather than JOBS_DIR itself so nothing
# else on the filesystem gets scanned as if it were a job.
RUNS_DIR = JOBS_DIR / "runs"
# Where a team's uv project sits inside their fork of this repo.
AGENT_SUBDIR = os.environ.get("AGENT_SUBDIR", "agent")

sqs = boto3.client("sqs", region_name=results.region())
table = results.table()


def task_dir(task_id: str) -> Path:
    """Resolve a task id to its definition.

    A trailing `#n` marks a repeat of the same task. The table is keyed on
    (submission_id, task_id), so four runs of one task need four distinct ids
    or they overwrite each other and the statistics vanish.
    """
    return TASKS_DIR / task_id.split("#", 1)[0]


def run_rollout(job: dict, slug: str) -> dict:
    task_path = task_dir(job["task_id"])
    if not task_path.is_dir():
        return {"status": "error", "error": f"unknown task {job['task_id']}"}

    # A key per rollout is what makes the token count attributable and
    # server-side. Rate limited so one runaway loop cannot starve the queue.
    key = mint_key(f"{job['submission_id']}--{job['task_id']}", rpm=RATE_LIMIT_RPM)

    started = time.monotonic()
    try:
        proc = subprocess.run(
            [
                "harbor", "run",
                "-p", str(task_path),
                "-a", "harness.submission_agent:Submission",
                "-m", MODEL,
                "-n", "1",
                # The job name is the directory harbor creates under -o, so this
                # is what makes each rollout a directly scannable job.
                "-o", str(RUNS_DIR),
                "--job-name", slug,
                "-y",
                "--ae", f"SUBMISSION_REPO_URL={job['repo_url']}",
                "--ae", f"SUBMISSION_COMMIT={job['commit']}",
                # A team forks this repo, so their agent is a directory in it
                # rather than the whole checkout.
                "--ae", f"SUBMISSION_SUBDIR={AGENT_SUBDIR}",
                "--ae", f"GATEWAY_URL={GATEWAY_URL}",
                "--ae", f"GATEWAY_API_KEY={key['key']}",
            ],
            capture_output=True,
            text=True,
            # harbor is installed as a uv tool, so the repo is not on its
            # sys.path and the submission adapter would not import.
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
        )
        duration = time.monotonic() - started

        result = parse_trial(RUNS_DIR / slug)
        result["duration_s"] = round(duration, 1)
        if result.get("status") == "error" and not result.get("error"):
            result["error"] = proc.stderr[-1000:]

        # Best effort: a submission that did not record its conversation is
        # still a graded submission, and losing the view is not worth losing
        # the score.
        trial = trial_dir(RUNS_DIR / slug)
        if trial:
            try:
                trajectory.convert(trial, agent="submission", version=job["commit"][:8], model=MODEL)
            except Exception as exc:  # noqa: BLE001
                print(f"!! trajectory: {exc}", file=sys.stderr, flush=True)

        # Tokens come from the gateway, never from the trial, because the
        # trial reports whatever participant code told it. Read even on
        # failure: a crashed rollout still spent what it spent.
        result.update(usage_for_key(key["key"]))
        return result
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:1000]}
    finally:
        # A leaked key stays billable and rate-limit-bearing, so retire it even
        # when the rollout blew up.
        delete_key(key["key"])


def trial_dir(job_dir: Path) -> Path | None:
    """The single trial inside a rollout's job, or None if it never got there."""
    results_files = sorted(job_dir.glob("*/result.json"))
    return results_files[0].parent if results_files else None


def parse_trial(job_dir: Path) -> dict:
    trial_path = trial_dir(job_dir)
    if trial_path is None:
        return {"status": "error", "error": "no trial result produced"}

    trial = json.loads((trial_path / "result.json").read_text())

    if trial.get("exception_info"):
        return {
            "status": "error",
            "error": trial["exception_info"]["exception_message"][:1000],
        }

    rewards = (trial.get("verifier_result") or {}).get("rewards") or {}
    # Binary: a task counts only when the verifier is fully satisfied. Partial
    # rewards are kept for feedback, not for scoring.
    passed = bool(rewards) and all(float(v) >= 1.0 for v in rewards.values())

    return {
        "status": "done",
        "passed": passed,
        "rewards": {k: str(v) for k, v in rewards.items()},
    }


def viewer_slug(submission_id: str, task_id: str) -> str:
    """The rollout's directory name, which is also its URL in the viewer.

    Flat rather than nested under team/submission because `harbor view` treats
    each immediate child of its root as one job, and refuses any job path that
    resolves outside that root -- so a symlink farm over a nested tree is
    rejected as traversal. The submission id already begins with the team, so
    grouping survives as a name prefix.

    `#` marks a repeat of the same task and is a URL fragment delimiter, so it
    becomes a dash or every link would truncate at the hash.
    """
    return f"{submission_id}__{task_id.replace('#', '-')}"


def record(job: dict, result: dict) -> None:
    table.put_item(Item=results.rollout_item(job, result))


def handle(message: dict) -> None:
    job = json.loads(message["Body"])
    print(f"-> {job['submission_id']} / {job['task_id']}", flush=True)

    slug = viewer_slug(job["submission_id"], job["task_id"])
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    result = run_rollout(job, slug)
    result["job_dir"] = str(RUNS_DIR / slug)
    record(job, result)

    # An infrastructure error is not a score. Recorded first so the row is never
    # missing, then raised so SQS redelivers and the retry overwrites this row
    # on the same key. Two failures reach the DLQ, which is where a genuinely
    # broken rollout belongs -- not on the board as a zero the team earned.
    if result.get("status") == "error":
        raise RuntimeError(result.get("error", "rollout errored"))

    print(f"<- {job['task_id']} done", flush=True)


def release(receipt_handle: str) -> None:
    """Hand the in-flight message straight back to the queue.

    ECS stops a task with SIGTERM and kills it shortly after, which is far less
    time than a rollout needs, so the work is lost either way. What must not be
    lost is the message: left alone it stays invisible for the whole visibility
    timeout before another worker can retry it, and every restart spends one of
    its few attempts. Resetting visibility to zero makes the retry immediate.
    """
    with contextlib.suppress(Exception):
        sqs.change_message_visibility(
            QueueUrl=QUEUE_URL,
            ReceiptHandle=receipt_handle,
            VisibilityTimeout=0,
        )


def ensure_base_image() -> None:
    """Build the image every task starts FROM, in the host daemon.

    The task Dockerfiles name a plain local tag rather than a registry, so there
    is no pull, no credentials and nothing to publish -- the base is built from
    tasks/base, which ships in this image. `docker build` streams its context
    from here to the daemon over the socket, so a path inside this container is
    fine (a bind mount would not be: those resolve on the host).

    Every replica calls this and only the first does any work; the rest hit the
    layer cache. Cheaper than coordinating, and correct if a host is replaced.
    """
    result = subprocess.run(
        ["docker", "build", "-q", "-t", "hmdyb-task-base:1", str(TASKS_DIR / "base")],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Not fatal here: every rollout would fail on it anyway, and the error
        # belongs on the rollout that hit it rather than in a startup crash loop.
        print(f"!! base image build failed: {result.stderr[-500:]}", file=sys.stderr, flush=True)
    else:
        print(f"base image ready: {result.stdout.strip()}", flush=True)


def main() -> int:
    print(f"polling {QUEUE_URL}", flush=True)
    ensure_base_image()

    # The message currently being worked on, so a shutdown signal can put it
    # back. One slot, because a worker handles one rollout at a time.
    in_flight: str | None = None

    def on_terminate(_signum, _frame):
        if in_flight:
            print("!! terminating, releasing in-flight rollout", file=sys.stderr, flush=True)
            release(in_flight)
        raise SystemExit(0)

    # Deploys and instance refreshes are routine during an event; each one lands
    # here rather than on a team's score.
    signal.signal(signal.SIGTERM, on_terminate)
    signal.signal(signal.SIGINT, on_terminate)

    while True:
        response = sqs.receive_message(
            QueueUrl=QUEUE_URL,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
        )
        for message in response.get("Messages", []):
            in_flight = message["ReceiptHandle"]
            try:
                handle(message)
            except Exception as exc:  # noqa: BLE001
                # Leave it on the queue: a retry may well succeed, and the
                # redrive policy bounds how many times that can happen.
                print(f"!! {exc}", file=sys.stderr, flush=True)
                continue
            finally:
                in_flight = None
            sqs.delete_message(
                QueueUrl=QUEUE_URL,
                ReceiptHandle=message["ReceiptHandle"],
            )


def _demo() -> None:
    """The slug is a contract: the worker names the symlink, the dashboard
    builds the same string into a URL. If they drift, every trace link 404s."""
    assert viewer_slug("a-385471d7", "incremental-dupes") == "a-385471d7__incremental-dupes"
    # The repeat suffix must not survive as `#`, which would truncate the URL.
    assert viewer_slug("a-385471d7", "incremental-dupes#3") == "a-385471d7__incremental-dupes-3"
    assert "#" not in viewer_slug("t-1", "x#9")
    print("ok")


if __name__ == "__main__":
    sys.exit(_demo() if os.environ.get("DEMO") else main())

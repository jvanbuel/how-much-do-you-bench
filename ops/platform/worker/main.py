"""The consumer: polls SQS, runs one rollout through Harbor, records it.

Deliberately a loop and not a framework. One message at a time, so concurrency
is replicas rather than threads: the number of rollouts in flight is the number
of copies of this process.

Cannot enqueue submissions. That belongs to the API, and the IAM policy
enforces the split.
"""

import contextlib
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from functools import cache
from pathlib import Path

import boto3

from common import results
from worker import submission_config, trajectory
from worker.gateway import delete_key, mint_key, usage_for_key

QUEUE_URL = os.environ.get("QUEUE_URL", "")
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

# A message that failed goes back to the queue with a delay rather than
# immediately: a rollout failing on something durable would otherwise spend all
# five of its attempts in a few seconds and reach the DLQ before the cause had
# time to clear. Doubling per attempt, capped well under the visibility timeout.
RETRY_BACKOFF_SEC = 30
MAX_BACKOFF_SEC = 900

# Harbor got far enough to write a trial, or it did not. The difference decides
# whether a failure is the rollout's or the runner's, so it is one string.
NO_TRIAL = "no trial result produced"


# Built on first use, not on import: the self-checks below exercise the parts
# with logic in them, and none of that needs AWS to exist.
@cache
def queue():
    return boto3.client("sqs", region_name=results.region())


@cache
def table():
    return results.table()


def task_dir(task_id: str) -> Path:
    """Resolve a task id to its definition.

    A trailing `#n` marks a repeat of the same task. The table is keyed on
    (submission_id, task_id), so four runs of one task need four distinct ids
    or they overwrite each other and the statistics vanish.
    """
    return TASKS_DIR / task_id.split("#", 1)[0]


def harbor_command(job: dict, slug: str, task_path: Path, config: Path | None, key: str) -> list[str]:
    """The rollout, as a command.

    `--config` when the submission brought a usable agent.yaml, `-a` when it did
    not. The two are alternatives: the config names the agent, so passing both
    would silently override the file the team was told decides what runs.

    Everything the config is *not* allowed to decide is a flag here, and flags
    win over the file: the task, the repeat count, the output directory and the
    agent environment belong to the runner.
    """
    command = [
        "harbor", "run",
        "-p", str(task_path),
        "-n", "1",
        # The job name is the directory harbor creates under -o, so this is what
        # makes each rollout a directly scannable job.
        "-o", str(RUNS_DIR),
        "--job-name", slug,
        "-y",
    ]
    if config is not None:
        command += ["--config", str(config)]
    else:
        command += ["-a", submission_config.SUBMISSION_ADAPTER, "-m", MODEL]

    return command + [
        "--ae", f"SUBMISSION_REPO_URL={job['repo_url']}",
        "--ae", f"SUBMISSION_COMMIT={job['commit']}",
        # A team forks this repo, so their agent is a directory in it rather
        # than the whole checkout.
        "--ae", f"SUBMISSION_SUBDIR={AGENT_SUBDIR}",
        "--ae", f"GATEWAY_URL={GATEWAY_URL}",
        "--ae", f"GATEWAY_API_KEY={key}",
    ]


def harbor_run(job: dict, slug: str, task_path: Path, config: Path | None, key: str):
    return subprocess.run(
        harbor_command(job, slug, task_path, config, key),
        capture_output=True,
        text=True,
        # harbor is installed as a uv tool, so the repo is not on its sys.path
        # and the submission adapter would not import.
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
    )


def run_rollout(job: dict, slug: str, attempt: int) -> dict:
    task_path = task_dir(job["task_id"])
    if not task_path.is_dir():
        return {"status": "error", "error": f"unknown task {job['task_id']}"}

    # Redelivery lands on the same slug, and Harbor will not resume a job
    # directory it did not finish writing -- a worker killed mid-rollout leaves
    # exactly that. The retry exists to get a clean run, so it starts from a
    # clean directory. The previous attempt's trace is not worth keeping: it is
    # the one that did not produce a result.
    shutil.rmtree(RUNS_DIR / slug, ignore_errors=True)

    with tempfile.TemporaryDirectory(prefix="submission-") as checkout:
        # The submitted commit decides the harness, because that is what
        # `agent.yaml` promises and what `just eval` runs locally. A config that
        # cannot be graded is a note on the result, not a failed rollout.
        config, agent, note = submission_config.resolve(job, Path(checkout), MODEL)
        if note:
            print(f"!! {job['task_id']}: {note}", file=sys.stderr, flush=True)

        # A key per rollout is what makes the token count attributable and
        # server-side. Rate limited so one runaway loop cannot starve the queue.
        # Inside the try/except: a gateway hiccup here is an infrastructure
        # failure like any other, and it belongs on a recorded row rather than
        # escaping to the poll loop unrecorded.
        try:
            key = mint_key(
                f"{job['submission_id']}--{job['task_id']}--{attempt}", rpm=RATE_LIMIT_RPM
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"minting a gateway key: {exc}"[:1000]}

        started = time.monotonic()
        try:
            proc = harbor_run(job, slug, task_path, config, key["key"])
            result = parse_trial(RUNS_DIR / slug)

            if rejected_config(config, proc, result):
                # The runner would not take the submitted config, and a config
                # the runner will not take must not cost a team the task: the
                # same rollout runs again on the pinned adapter, which is their
                # own entrypoint. Without this a config Harbor dislikes fails
                # every rollout of every submission that carries it, five times
                # each, all the way to the dead-letter queue.
                note = f"agent.yaml ignored: the runner refused it ({proc.stderr.strip()[-300:]})"
                print(f"!! {job['task_id']}: {note}", file=sys.stderr, flush=True)
                # Harbor will not resume a job directory whose config changed,
                # so the first attempt's directory has to go before the second.
                shutil.rmtree(RUNS_DIR / slug, ignore_errors=True)
                agent, config = "submission", None
                proc = harbor_run(job, slug, task_path, None, key["key"])
                result = parse_trial(RUNS_DIR / slug)

            duration = time.monotonic() - started
            result["duration_s"] = round(duration, 1)
            result["agent"] = agent
            if note:
                result["notes"] = note[:1000]
            if result.get("status") == "error" and not result.get("error"):
                result["error"] = proc.stderr[-1000:]

            # Best effort: a submission that did not record its conversation is
            # still a graded submission, and losing the view is not worth losing
            # the score.
            trial = trial_dir(RUNS_DIR / slug)
            if trial:
                try:
                    trajectory.convert(trial, agent=agent, version=job["commit"][:8], model=MODEL)
                except Exception as exc:  # noqa: BLE001
                    print(f"!! trajectory: {exc}", file=sys.stderr, flush=True)

            result.update(metering(key["key"], result))
            return result
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:1000]}
        finally:
            # A leaked key stays billable and rate-limit-bearing, so retire it
            # even when the rollout blew up.
            with contextlib.suppress(Exception):
                delete_key(key["key"])


def rejected_config(config: Path | None, proc: subprocess.CompletedProcess, result: dict) -> bool:
    """Did the runner refuse the submitted config before anything ran?

    The signature is a failed run that produced no trial at all: a rollout that
    started and then died leaves one behind, with whatever the agent managed.
    Distinguishing the two is what keeps this from re-running a rollout that
    genuinely failed -- that is the queue's job, not this function's.
    """
    return (
        config is not None
        and proc.returncode != 0
        and result.get("error") == NO_TRIAL
    )


def metering(key: str, result: dict) -> dict:
    """Token counts from the gateway, never from the trial.

    The trial reports whatever participant code told it. Read even on failure: a
    crashed rollout still spent what it spent.

    Best effort, deliberately. Metering is not the grade, and a graded rollout
    thrown away because the admin API blinked costs the team a task and the
    fleet another 142 seconds. The row carries the failure instead, so a
    suspiciously cheap submission can be told apart from a genuinely cheap one.
    """
    try:
        usage = usage_for_key(key)
    except Exception as exc:  # noqa: BLE001
        print(f"!! metering: {exc}", file=sys.stderr, flush=True)
        return {"metering": f"unread: {type(exc).__name__}"}

    if result.get("status") == "done" and not usage.get("requests"):
        usage["metering"] = "no requests recorded against this key"
    return usage


def trial_dir(job_dir: Path) -> Path | None:
    """The single trial inside a rollout's job, or None if it never got there."""
    results_files = sorted(job_dir.glob("*/result.json"))
    return results_files[0].parent if results_files else None


def parse_trial(job_dir: Path) -> dict:
    trial_path = trial_dir(job_dir)
    if trial_path is None:
        return {"status": "error", "error": NO_TRIAL}

    trial = json.loads((trial_path / "result.json").read_text())

    if trial.get("exception_info"):
        message = trial["exception_info"]["exception_message"][:1000]
        # Whose failure it was decides whether it is worth retrying. Once the
        # agent has started, the environment built and the harness installed, so
        # what blew up is the submitted code -- a team whose agent raises on one
        # task would otherwise be retried five times, dead-lettered, and left
        # unfinished, which keeps the whole submission off the board. It ran and
        # it failed: that is a score of zero, not an incident.
        if (trial.get("agent_execution") or {}).get("started_at"):
            return {"status": "done", "passed": False, "rewards": {}, "error": message}
        return {"status": "error", "error": message}

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
    table().put_item(Item=results.rollout_item(job, result))


def handle(message: dict) -> None:
    job = json.loads(message["Body"])
    attempt = int(message.get("Attributes", {}).get("ApproximateReceiveCount", 1))
    print(f"-> {job['submission_id']} / {job['task_id']} (attempt {attempt})", flush=True)

    slug = viewer_slug(job["submission_id"], job["task_id"])
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    result = run_rollout(job, slug, attempt)
    result["job_dir"] = str(RUNS_DIR / slug)
    record(job, result)

    # An infrastructure error is not a score. Recorded first so the row is never
    # missing, then raised so SQS redelivers and the retry overwrites this row
    # on the same key. Five failures reach the DLQ, and the row left behind says
    # `error`, which keeps the submission off the frontier rather than putting
    # it there with a zero the team never earned. `just ops::redrive` puts those
    # rollouts back on the queue.
    if result.get("status") == "error":
        raise RuntimeError(result.get("error", "rollout errored"))

    print(f"<- {job['task_id']} done", flush=True)


def release(receipt_handle: str, delay: int = 0) -> None:
    """Hand the in-flight message back to the queue.

    ECS stops a task with SIGTERM and kills it shortly after, which is far less
    time than a rollout needs, so the work is lost either way. What must not be
    lost is the message: left alone it stays invisible for the whole visibility
    timeout -- forty minutes -- before another worker can retry it, and every
    restart spends one of its few attempts.

    Zero on shutdown, where another worker can take it immediately. A backoff on
    failure, where retrying instantly would just burn the attempts.
    """
    with contextlib.suppress(Exception):
        queue().change_message_visibility(
            QueueUrl=QUEUE_URL,
            ReceiptHandle=receipt_handle,
            VisibilityTimeout=delay,
        )


def backoff(attempt: int) -> int:
    return min(RETRY_BACKOFF_SEC * 2 ** max(attempt - 1, 0), MAX_BACKOFF_SEC)


def ensure_base_image() -> None:
    """Build the image every task starts FROM, in the host daemon.

    The task Dockerfiles name a plain local tag rather than a registry, so there
    is no pull, no credentials and nothing to publish -- the base is built from
    tasks/base, which ships in this image. `docker build` streams its context
    from here to the daemon over the socket, so a path inside this container is
    fine (a bind mount would not be: those resolve on the host).

    Serialized on a lock, and that is the whole point rather than tidiness: twelve
    replicas building at once produced twelve distinct base images, three per
    host, so every task image built afterwards shared nothing with its
    neighbours. That is what filled a 98GB disk before the shared base existed.
    Holding the lock means the first build populates the daemon cache and the
    replicas behind it resolve to the identical image id.

    The lock lives on the shared jobs filesystem, so it also spaces out the four
    hosts. Cold start pays for that once, in series.
    """
    lock_path = JOBS_DIR / ".base-build.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = subprocess.run(
            ["docker", "build", "-q", "-t", "hmdyb-task-base:1", str(TASKS_DIR / "base")],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            # Not fatal here: every rollout would fail on it anyway, and the
            # error belongs on the rollout that hit it rather than in a startup
            # crash loop.
            print(f"!! base image build failed: {result.stderr[-500:]}", file=sys.stderr, flush=True)
        else:
            print(f"base image ready: {result.stdout.strip()}", flush=True)


def main() -> int:
    if not QUEUE_URL:
        # Read from the environment rather than required at import, so the
        # self-checks can run without one; still not optional to actually run.
        raise SystemExit("QUEUE_URL is not set")

    # Which tasks this image carries, said out loud once. The API enqueues the
    # list terraform derived from the tree at apply time, and the worker resolves
    # each one against the tasks baked into this image -- deliberately, so a team
    # cannot ship its own verifier. Applying without `just deploy` therefore
    # enqueues rollouts nothing can run, and this line is how that is spotted in
    # the first minute rather than in the results.
    tasks = sorted(p.parent.name for p in TASKS_DIR.glob("*/task.toml"))
    print(f"polling {QUEUE_URL}", flush=True)
    print(f"{len(tasks)} tasks in this image: {', '.join(tasks)}", flush=True)
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
        response = queue().receive_message(
            QueueUrl=QUEUE_URL,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
            # How many times this rollout has already been delivered, which is
            # what the retry backoff and the key alias are keyed on.
            AttributeNames=["ApproximateReceiveCount"],
        )
        for message in response.get("Messages", []):
            in_flight = message["ReceiptHandle"]
            try:
                handle(message)
            except Exception as exc:  # noqa: BLE001
                # Back on the queue now rather than in forty minutes: the
                # visibility timeout is sized for a rollout that is still
                # running, and this one is not.
                attempt = int(message.get("Attributes", {}).get("ApproximateReceiveCount", 1))
                delay = backoff(attempt)
                print(f"!! {exc} (retry in {delay}s)", file=sys.stderr, flush=True)
                release(message["ReceiptHandle"], delay)
                continue
            finally:
                in_flight = None
            queue().delete_message(
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

    # Backoff climbs and then stops, well inside the visibility timeout: a
    # message released for longer than that is invisible for the wrong reason.
    assert [backoff(n) for n in (1, 2, 3)] == [30, 60, 120]
    assert backoff(20) == MAX_BACKOFF_SEC

    job = {"repo_url": "https://github.com/t/a", "commit": "c" * 40, "task_id": "x"}
    # A submitted config names the agent, so -a and -m must not also be there.
    with_config = harbor_command(job, "s", Path("/tasks/x"), Path("/tmp/c.yaml"), "sk-1")
    assert "--config" in with_config and "-a" not in with_config and "-m" not in with_config
    # Without one, the pinned adapter runs the team's own entrypoint.
    without = harbor_command(job, "s", Path("/tasks/x"), None, "sk-1")
    assert without[without.index("-a") + 1] == submission_config.SUBMISSION_ADAPTER
    # The runner owns the task, the repeat count and the output directory in
    # both cases: a config that set them would be grading something else.
    for command in (with_config, without):
        assert command[command.index("-n") + 1] == "1"
        assert command[command.index("-p") + 1] == "/tasks/x"

    # Metering never turns a graded rollout into an infrastructure failure.
    # Pointed at a port nothing listens on rather than at the default admin URL,
    # which answers for any key when `just gateway` happens to be running: the
    # check would then spend a full settle timeout and assert the wrong branch.
    graded = {"status": "done", "passed": True}
    from worker import gateway

    was = gateway.BASE
    gateway.BASE = "http://127.0.0.1:1"  # nothing listens on port 1
    try:
        assert metering("sk-unreachable", graded)["metering"].startswith("unread:")
        assert graded["status"] == "done"
    finally:
        gateway.BASE = was

    # A config the runner refuses is retried once on the pinned adapter, so one
    # unusable agent.yaml costs a note rather than every rollout carrying it.
    ok = subprocess.CompletedProcess([], 0, "", "")
    failed = subprocess.CompletedProcess([], 1, "", "unknown field")
    config = Path("/tmp/c.yaml")
    assert rejected_config(config, failed, {"status": "error", "error": NO_TRIAL})
    # Not when the rollout ran and failed on its own merits: that is the queue's
    # business, and re-running it here would grade a different agent.
    assert not rejected_config(config, failed, {"status": "error", "error": "agent timed out"})
    assert not rejected_config(config, ok, {"status": "done", "passed": False})
    # And never when the pinned adapter was already what ran.
    assert not rejected_config(None, failed, {"status": "error", "error": NO_TRIAL})

    print("worker ok")


if __name__ == "__main__":
    sys.exit(_demo() if os.environ.get("DEMO") else main())

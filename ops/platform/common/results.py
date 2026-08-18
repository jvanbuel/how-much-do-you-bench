"""The results table: its shape, and the two ways it is used.

This is the contract between the two services. The worker writes rows, the API
reads them, and if they disagree about a field name the disagreement shows up
during the event rather than in a test. Keeping the shape in one module is the
reason `common` exists at all.

Rows are keyed `submission_id` / `task_id`. The sentinel task id `_meta` holds
the submission itself; every other row is one rollout.
"""

import os
from datetime import UTC, datetime
from typing import Any

import boto3

META = "_meta"
# One row per team, holding how many submissions it has spent. A row rather than
# a count of `_meta` rows because counting is a read: two submits arriving
# together both read "4 used" and both proceed, and the sixth run of a
# twenty-one task suite is not free.
COUNTER = "_counter"


class SubmissionsExhausted(Exception):
    """The team has spent every submission it is allowed."""

# Written by the worker, read by the API. Absent keys are omitted rather than
# stored as null, because DynamoDB charges for what you write.
ROLLOUT_FIELDS = (
    "tokens_in",
    "tokens_out",
    "job_dir",
    "error",
    "rewards",
    "requests",
    # Which harness actually ran, from the submission's own agent.yaml.
    "agent",
    # Why it was not the one they asked for, when it was not.
    "notes",
    # Set only when the gateway's token count is not to be trusted, so a
    # suspiciously cheap row can be told apart from a genuinely cheap one.
    "metering",
)


def region() -> str:
    """Explicit, because a profile's own region silently outranks AWS_REGION
    and the resources live beside Gemma in eu-central-1."""
    return os.environ.get("BENCHMARK_REGION", "eu-central-1")


def table(name: str | None = None):
    name = name or os.environ["RESULTS_TABLE"]
    return boto3.resource("dynamodb", region_name=region()).Table(name)


def scan(tbl) -> list[dict]:
    """Every row, following pagination.

    At ~50 submissions x 20 tasks this is a single page. Revisit at ten times
    that, when it becomes worth a secondary index.
    """
    items: list[dict] = []
    kwargs: dict[str, Any] = {}
    while True:
        page = tbl.scan(**kwargs)
        items.extend(page["Items"])
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def claim_submission(tbl, team: str, limit: int) -> int:
    """Spend one of the team's submissions, and say how many are now used.

    The check and the increment are one conditional update, so the limit holds
    when two people on the same team hit submit at the same moment.
    """
    try:
        response = tbl.update_item(
            Key={"submission_id": f"_team-{team}", "task_id": COUNTER},
            UpdateExpression="SET used = if_not_exists(used, :zero) + :one",
            ConditionExpression="attribute_not_exists(used) OR used < :limit",
            ExpressionAttributeValues={":zero": 0, ":one": 1, ":limit": limit},
            ReturnValues="UPDATED_NEW",
        )
    except tbl.meta.client.exceptions.ConditionalCheckFailedException as exc:
        raise SubmissionsExhausted(team) from exc
    return int(response["Attributes"]["used"])


def meta_item(submission_id: str, team: str, repo_url: str, commit: str, task_count: int) -> dict:
    return {
        "submission_id": submission_id,
        "task_id": META,
        "team": team,
        "repo_url": repo_url,
        "commit": commit,
        "task_count": task_count,
        "created_at": datetime.now(UTC).isoformat(),
    }


def rollout_item(job: dict, result: dict) -> dict:
    """One finished rollout, ready to write.

    Floats are stored as strings: DynamoDB has no float type, and rounding a
    duration or a spend into a Decimal loses precision in a way that only
    surfaces when the numbers are added up.
    """
    item = {
        "submission_id": job["submission_id"],
        "task_id": job["task_id"],
        "team": job["team"],
        "commit": job["commit"],
        "status": result.get("status"),
        "passed": bool(result.get("passed")),
        "duration_s": str(result.get("duration_s") or 0),
    }
    for field in ROLLOUT_FIELDS:
        if result.get(field) is not None:
            item[field] = result[field]
    if result.get("spend_usd") is not None:
        item["spend_usd"] = str(result["spend_usd"])
    return item


def errored(rollout: dict) -> bool:
    """A rollout that did not run, as opposed to one that ran and failed.

    The split is made in `parse_trial`, on whether the agent ever started. A
    submitted agent that raises is not an incident to retry: it ran, it scored
    zero, and it is recorded as done with `passed=False` so the submission can
    still be ranked. Only a failure before the agent started reaches here.

    The worker records the row and then re-raises, so an infrastructure failure
    is visible while SQS retries it. After the last attempt the row is all that
    is left, and counting it as a completed task would put the submission on the
    board with a zero it never earned -- the one thing the retry policy exists
    to prevent. It stays unfinished instead, which is the truth, and
    `just ops::redrive` is how it gets finished.
    """
    return rollout.get("status") == "error"


def summarise(items: list[dict]) -> list[dict]:
    """Collapse rows into one record per submission, as the dashboard wants it."""
    meta: dict[str, dict] = {}
    rollouts: dict[str, list[dict]] = {}

    for item in items:
        if item["task_id"] == META:
            meta[item["submission_id"]] = item
        elif item["task_id"] != COUNTER:
            rollouts.setdefault(item["submission_id"], []).append(item)

    submissions = []
    for submission_id, info in meta.items():
        rows = rollouts.get(submission_id, [])
        done = [r for r in rows if not errored(r)]
        failed = [r for r in rows if errored(r)]
        submissions.append(
            {
                "submission_id": submission_id,
                "team": info["team"],
                "commit": info["commit"][:8],
                "created_at": info.get("created_at"),
                "task_count": int(info.get("task_count", 0)),
                "completed": len(done),
                # Rollouts that never produced a result. Shown rather than
                # folded into the score, because nobody earned them.
                "errored": len(failed),
                # A token count the gateway could not vouch for makes the
                # efficiency ranking a guess, so the board says so.
                "unmetered": sum(1 for r in rows if r.get("metering")),
                "passed": sum(1 for r in done if r.get("passed")),
                "tokens": sum(
                    int(r.get("tokens_in") or 0) + int(r.get("tokens_out") or 0)
                    for r in done
                ),
                # Summed, not wall clock: rollouts of one submission run on
                # whichever workers are free, so elapsed time measures how busy
                # the fleet was. The sum is the agent time the submission cost,
                # which is the part that belongs to the submission.
                "duration_s": sum(float(r.get("duration_s") or 0) for r in done),
                "tasks": sorted(
                    (
                        {
                            "task_id": r["task_id"],
                            "passed": bool(r.get("passed")),
                            "status": r.get("status"),
                            "duration_s": float(r.get("duration_s") or 0),
                            "job_dir": r.get("job_dir"),
                            "error": r.get("error"),
                            # Which harness ran, and why it was not the one the
                            # submission asked for.
                            "agent": r.get("agent"),
                            "notes": r.get("notes") or r.get("metering"),
                        }
                        # Failed rollouts included: they are the ones a team
                        # needs to see, and they are why the submission is not
                        # finished yet.
                        for r in rows
                    ),
                    key=lambda t: t["task_id"],
                ),
            }
        )
    return submissions


def _demo() -> None:
    """The completed count decides whether a submission is ranked at all, so
    what does and does not count towards it is worth pinning down."""
    rows = [
        meta_item("a-1", "alpha", "https://github.com/alpha/a", "c" * 40, task_count=3),
        {"submission_id": "a-1", "task_id": "one", "status": "done", "passed": True,
         "tokens_in": 100, "tokens_out": 20, "duration_s": "12"},
        {"submission_id": "a-1", "task_id": "two", "status": "done", "passed": False,
         "tokens_in": 300, "tokens_out": 40, "duration_s": "30"},
        # Never ran: five attempts, then the DLQ. Not a task the team failed.
        {"submission_id": "a-1", "task_id": "three", "status": "error",
         "error": "no trial result produced", "duration_s": "0"},
        # Another team's counter row, which is bookkeeping rather than a rollout.
        {"submission_id": "_team-alpha", "task_id": COUNTER, "used": 2},
    ]

    [summary] = summarise(rows)
    assert summary["completed"] == 2, summary["completed"]
    assert summary["errored"] == 1
    assert summary["passed"] == 1
    # 2 of 3 completed, so it is still running: an unrun rollout must not reach
    # the board as a zero, and that means it cannot be ranked yet either.
    assert summary["completed"] < summary["task_count"]
    # Cost and time come from rollouts that happened.
    assert summary["tokens"] == 460
    assert summary["duration_s"] == 42
    # The failed rollout still shows in the detail, which is where a team looks.
    assert [t["task_id"] for t in summary["tasks"]] == ["one", "three", "two"]
    assert summary["tasks"][1]["error"] == "no trial result produced"

    # Once it is retried successfully the row is overwritten on the same key,
    # and the submission is finished.
    rows[3] = {"submission_id": "a-1", "task_id": "three", "status": "done",
               "passed": True, "tokens_in": 10, "tokens_out": 5, "duration_s": "20"}
    [retried] = summarise(rows)
    assert retried["completed"] == 3 == retried["task_count"]
    assert retried["errored"] == 0
    assert retried["passed"] == 2

    # A row the gateway could not vouch for is counted, not hidden.
    rows[1] = {**rows[1], "metering": "unread: ConnectionError"}
    assert summarise(rows)[0]["unmetered"] == 1

    print("results ok")


if __name__ == "__main__":
    _demo()

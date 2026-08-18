"""The producer: accepts submissions, enqueues rollouts, serves the dashboard.

Deliberately cannot consume the queue or write rollout rows. Those belong to
the worker, and the IAM policy enforces the split rather than trusting this
module to behave.
"""

import hmac
import json
import os
import re
import time
import uuid
from pathlib import Path

import boto3
from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from common import results
from common import repo
from common.scoring import accuracy_board, contenders, pareto

QUEUE_URL = os.environ["QUEUE_URL"]
TASKS = os.environ.get("TASKS", "incremental-dupes").split(",")
MAX_SUBMISSIONS = int(os.environ.get("MAX_SUBMISSIONS", "5"))
# Where the trajectory viewer is served. Empty when it is not deployed, which
# the dashboard reads as "render no trace links" rather than dead ones.
VIEWER_URL = os.environ.get("VIEWER_URL", "").rstrip("/")
# The team name is a key prefix, a directory name on EFS and a path segment in
# every trace URL. Constraining it here is cheaper than escaping it three times.
TEAM = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
COMMIT = re.compile(r"[0-9a-f]{40}")

sqs = boto3.client("sqs", region_name=results.region())
table = results.table()

app = FastAPI()


class SubmitRequest(BaseModel):
    team: str | None = None
    repo_url: str
    commit: str


def _repo_url(url: str) -> str:
    """The submission's repository, or a 400 saying which part is wrong."""
    try:
        return repo.normalise(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# The team keys, read from SSM rather than held in config: terraform writes them
# and only the account can decrypt them. Cached briefly so a burst of submits is
# not a burst of SSM calls, and short enough that deleting a team's parameter
# takes effect within a minute rather than at the next deploy.
KEY_PATH = os.environ.get("TEAM_KEY_PATH", "/de-benchmark/team-keys")
KEY_TTL = 60.0
_keys: dict[str, str] = {}
_keys_read_at = 0.0

ssm = boto3.client("ssm", region_name=results.region())


def _team_for(token: str) -> str | None:
    """Which team owns this key, or None.

    The token is the identity. A team name in the request body would be a claim
    the caller makes about itself, which is what let anyone spend another team's
    five submissions.
    """
    global _keys, _keys_read_at

    if time.monotonic() - _keys_read_at > KEY_TTL or not _keys:
        found: dict[str, str] = {}
        paginator = ssm.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=KEY_PATH, WithDecryption=True):
            for parameter in page["Parameters"]:
                name = parameter["Name"].rsplit("/", 1)[-1]
                if not TEAM.fullmatch(name):
                    print(f"!! ignoring team key with an unusable name: {name}", flush=True)
                    continue
                found[parameter["Value"]] = name
        # Only replace a working set with a non-empty one: an SSM blip should
        # not lock every team out mid-event.
        if found:
            _keys = found
        _keys_read_at = time.monotonic()

    return _match(token, _keys)


def _match(token: str, keys: dict[str, str]) -> str | None:
    """The team whose key this is, compared without leaking where it differs.

    compare_digest against each rather than a dict lookup: the timing of a dict
    hit is not something to hand someone with five hours and a leaderboard.
    """
    for key, team in keys.items():
        if hmac.compare_digest(token, key):
            return team
    return None


def _authenticated(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "submit needs your team key: Authorization: Bearer sk-...")
    team = _team_for(authorization.split(" ", 1)[1].strip())
    if team is None:
        raise HTTPException(403, "that key is not a team key for this event")
    return team


@app.post("/submit")
def submit(request: SubmitRequest, authorization: str = Header(default=None)):
    team = _authenticated(authorization)
    # The body may still name a team, because `just submit your-team` reads
    # naturally and a mismatch is worth saying out loud rather than ignoring.
    if team and team != team:
        raise HTTPException(403, f"that key belongs to {team}, not {team}")
    # Full hash only. GitHub serves a fetch-by-object request for a full SHA
    # and nothing else, so an abbreviated one is not a shorter way to say the
    # same thing: it is twenty rollouts that fail on checkout twenty minutes
    # from now. `just submit` derives this, which is why it exists.
    if not COMMIT.fullmatch(request.commit):
        raise HTTPException(
            400,
            "commit must be the full 40-character SHA. Submit with: just submit your-team",
        )
    repo_url = _repo_url(request.repo_url)

    # Spent before anything is enqueued, and spent atomically: the limit is the
    # only thing standing between one team and the whole fleet.
    try:
        used = results.claim_submission(table, team, MAX_SUBMISSIONS)
    except results.SubmissionsExhausted:
        raise HTTPException(
            429, f"team {team} has used all {MAX_SUBMISSIONS} submissions"
        ) from None

    submission_id = f"{team}-{uuid.uuid4().hex[:8]}"

    # Written before enqueueing, so a submission is never invisible while its
    # rollouts are already running.
    table.put_item(
        Item=results.meta_item(
            submission_id=submission_id,
            team=team,
            repo_url=repo_url,
            commit=request.commit,
            task_count=len(TASKS),
        )
    )

    for task_id in TASKS:
        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(
                {
                    "submission_id": submission_id,
                    "task_id": task_id,
                    "team": team,
                    "repo_url": repo_url,
                    "commit": request.commit,
                }
            ),
        )

    return {
        "submission_id": submission_id,
        "tasks_queued": len(TASKS),
        "submissions_remaining": MAX_SUBMISSIONS - used,
    }


# What the load balancer checks. Deliberately touches nothing: pointed at
# /results it took a full table scan every thirty seconds per target, and a
# throttled scan would have taken the dashboard down for being unhealthy.
@app.get("/health")
def health():
    return {"ok": True}


@app.get("/results")
def results_endpoint():
    submissions = results.summarise(results.scan(table))

    # A submission still running has an artificially low token count and an
    # artificially low score, so it can neither dominate nor be dominated yet.
    # A rollout that errored does not count as completed, so a submission with
    # one waits here rather than being ranked on a task nobody ran.
    def finished(s: dict) -> bool:
        return s["completed"] >= s["task_count"] > 0

    scored = [s for s in submissions if finished(s)]
    running = [s for s in submissions if not finished(s)]
    return {
        "submissions": accuracy_board(submissions),
        "pareto": pareto(scored),
        "contenders": contenders(running, scored),
        "viewer_url": VIEWER_URL,
    }


# The built Svelte app. Mounted last so it does not shadow the API routes.
#
# no-cache means revalidate, not don't store: with the ETag StaticFiles already
# sends, an unchanged file costs a 304. Without it browsers apply heuristic
# freshness to index.html and a tab left open across a deploy keeps asking for
# a bundle hash that no longer exists.
class Dashboard(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["cache-control"] = "no-cache"
        return response


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.is_dir():
    app.mount("/", Dashboard(directory=STATIC_DIR, html=True), name="dashboard")


def _demo() -> None:
    keys = {"sk-alpha-key": "alpha", "sk-beta-key": "beta"}
    assert _match("sk-alpha-key", keys) == "alpha"
    assert _match("sk-beta-key", keys) == "beta"
    assert _match("sk-alpha-ke", keys) is None      # a prefix is not a key
    assert _match("sk-alpha-keyy", keys) is None
    assert _match("", keys) is None
    assert _match("sk-alpha-key", {}) is None

    # Seeded first: _authenticated reads SSM when the cache is cold, and this
    # check is about the matching rather than about having credentials.
    global _keys, _keys_read_at
    _keys, _keys_read_at = keys, time.monotonic()

    for header in (None, "", "sk-alpha-key", "Basic sk-alpha-key", "Bearer nope"):
        try:
            _authenticated(header)
            raise AssertionError(f"accepted {header!r}")
        except HTTPException:
            pass

    assert _authenticated("Bearer sk-alpha-key") == "alpha"
    assert _authenticated("bearer sk-beta-key") == "beta"  # header names are case-insensitive
    print("api ok")


if __name__ == "__main__":
    _demo()

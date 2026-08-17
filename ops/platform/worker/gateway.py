"""Client for the LiteLLM gateway's key-management API.

A key is minted per rollout and retired after it, which is what makes token
counts attributable server-side. Anything the agent reports is self-reported by
the party being scored.

These routes need Postgres; without DATABASE_URL the proxy answers
/key/generate with "DB not connected".
"""

import os
import time
import uuid
from typing import Any

import requests

BASE = os.environ.get("GATEWAY_ADMIN_URL", "http://127.0.0.1:4000").rstrip("/")
MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "sk-local-dev")
MODEL = os.environ.get("MODEL", "gemma")

TIMEOUT = 30
HEADERS = {"Authorization": f"Bearer {MASTER_KEY}"}

# The spend log is written from a buffered background task, so the last requests
# of a rollout are usually not in it at the moment the rollout ends. Reading
# once undercounts, and undercounting is winning on the efficiency board, so the
# read waits for the count to stop moving instead.
SETTLE_INTERVAL = 3.0
SETTLE_TIMEOUT = 30.0

# /spend/logs pages in some versions and returns a bare list in others. Asking
# for a page size we know we would notice hitting is what makes the difference
# visible rather than silent.
PAGE_SIZE = 500
MAX_PAGES = 40


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        f"{BASE}{path}", json=payload, headers=HEADERS, timeout=TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def _get(path: str, params: dict[str, Any]) -> Any:
    response = requests.get(
        f"{BASE}{path}", params=params, headers=HEADERS, timeout=TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def mint_key(alias: str, rpm: int | None = None) -> dict:
    """One key per rollout, rate limited so a runaway loop cannot starve the queue.

    Aliases are unique in LiteLLM, and a rollout can be retried: a worker killed
    with SIGKILL never reaches the `finally` that retires its key, and the alias
    it holds would then reject every retry. A suffix on collision keeps the
    readable name and survives the leak.
    """
    payload: dict[str, Any] = {"key_alias": alias, "models": [MODEL]}
    if rpm is not None:
        payload["rpm_limit"] = rpm
    try:
        return _post("/key/generate", payload)
    except requests.HTTPError as exc:
        response = exc.response
        if response is None or response.status_code >= 500 or "alias" not in response.text.lower():
            raise
        payload["key_alias"] = f"{alias}--{uuid.uuid4().hex[:6]}"
        return _post("/key/generate", payload)


def _rows(key: str) -> list[dict]:
    """Every request this key made, over as many pages as the gateway serves."""
    rows: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        payload = _get("/spend/logs", {"api_key": key, "page": page, "page_size": PAGE_SIZE})
        batch = payload.get("data", []) if isinstance(payload, dict) else payload
        if not isinstance(batch, list):
            break
        rows.extend(batch)
        # A version that ignores the paging parameters returns the same rows for
        # every page, so stop unless the page was full and new.
        if len(batch) < PAGE_SIZE:
            break
    return rows


def _totals(rows: list[dict]) -> dict[str, Any]:
    return {
        "tokens_in": sum(int(r.get("prompt_tokens") or 0) for r in rows),
        "tokens_out": sum(int(r.get("completion_tokens") or 0) for r in rows),
        "spend_usd": round(sum(float(r.get("spend") or 0.0) for r in rows), 6),
        "requests": len(rows),
    }


def usage_for_key(key: str) -> dict[str, Any]:
    """Server-side token counts for one key, once the log has stopped growing.

    /key/info carries spend but no token breakdown, so the per-request spend
    log is the source. Counts are the gateway's, not the agent's.
    """
    totals = _totals(_rows(key))
    deadline = time.monotonic() + SETTLE_TIMEOUT

    while time.monotonic() < deadline:
        time.sleep(SETTLE_INTERVAL)
        again = _totals(_rows(key))
        if again == totals:
            return totals
        totals = again

    # Still moving when the budget ran out: report what is there, flagged, so a
    # suspiciously cheap row can be told apart from a genuinely cheap one.
    totals["metering"] = "still settling at read time"
    return totals


def delete_key(key: str) -> None:
    _post("/key/delete", {"keys": [key]})

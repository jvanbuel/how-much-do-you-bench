"""Turn a rollout's conversation into the file the trajectory viewer renders.

Harbor never writes a trajectory itself. Every built-in adapter converts its own
agent's native log into ATIF and drops it in the trial's `agent/` directory, and
a trial without that file simply has no trajectory to show.

Ours converts what the submission recorded at TRAJECTORY_PATH: the harness is
the only thing that knows what it actually sent, so it is the thing that says
so. The alternative, rebuilding the conversation from the gateway's request
log, meant guessing which request carried the full history -- a heuristic that
breaks as soon as a harness summarises and resets its context.
"""

import json
from pathlib import Path
from typing import Any

# ATIF roles. A `tool` message is not a step: it is the result of the call that
# asked for it, so it is folded into that step's observation.
SOURCES = {"system": "system", "user": "user", "assistant": "agent"}


def _tool_calls(message: dict) -> list[dict]:
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            # OpenAI sends arguments as a JSON string; ATIF wants the object.
            # A model that emits malformed JSON still produced a real call, so
            # keep the text rather than dropping the step.
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"raw": arguments}
        calls.append(
            {
                "tool_call_id": call.get("id") or "",
                "function_name": function.get("name") or "",
                "arguments": arguments if isinstance(arguments, dict) else {},
            }
        )
    return calls


def steps(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for message in messages:
        role = message.get("role")

        if role == "tool":
            if out and out[-1]["source"] == "agent":
                observation = out[-1].setdefault("observation", {"results": []})
                observation["results"].append(
                    {
                        "source_call_id": message.get("tool_call_id"),
                        "content": message.get("content"),
                    }
                )
            continue

        source = SOURCES.get(role)
        if source is None:
            continue

        step: dict[str, Any] = {
            "step_id": len(out) + 1,
            "source": source,
            # A tool-calling turn often has no prose, and `message` is required.
            "message": message.get("content") or "",
        }
        calls = _tool_calls(message)
        if calls:
            step["tool_calls"] = calls
        out.append(step)
    return out


def build(conversation: dict, *, agent: str, version: str, model: str) -> dict:
    return {
        "schema_version": "ATIF-v1.7",
        "agent": {
            "name": agent,
            "version": version,
            "model_name": model,
            "tool_definitions": conversation.get("tools") or None,
        },
        "steps": steps(conversation.get("messages") or []),
    }


def convert(trial_dir: Path, **kwargs) -> bool:
    """Turn the submission's own record into the file the viewer reads.

    False when there is nothing at TRAJECTORY_PATH, which covers both cases: a
    team that replaced the harness and dropped `record()`, and a team graded on
    an off-the-shelf harness, whose Harbor adapter has already written its own
    trajectory. Returning early rather than writing an empty one is what keeps
    this from overwriting that.
    """
    source = trial_dir / "agent" / "messages.json"
    if not source.is_file():
        return False

    conversation = json.loads(source.read_text())
    if not conversation.get("messages"):
        return False

    (trial_dir / "agent" / "trajectory.json").write_text(
        json.dumps(build(conversation, **kwargs), indent=2)
    )
    return True


def _demo() -> None:
    """Checked against Harbor's own validator, so the schema cannot drift here
    without the check failing."""
    conversation = {
        "messages": [
            {"role": "system", "content": "You fix data pipelines."},
            {"role": "user", "content": "The orders mart has duplicates."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "bash", "arguments": '{"cmd": "ls /app"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "models/ data/"},
        ],
        "tools": [{"type": "function", "function": {"name": "bash"}}],
    }

    doc = build(conversation, agent="submission", version="8c1f0d8", model="gemma")

    assert [s["source"] for s in doc["steps"]] == ["system", "user", "agent"]
    # The tool result folded into the call's step rather than becoming its own.
    assert doc["steps"][2]["observation"]["results"][0]["source_call_id"] == "call_1"
    # The argument string was parsed into an object.
    assert doc["steps"][2]["tool_calls"][0]["arguments"] == {"cmd": "ls /app"}
    from harbor.utils.trajectory_validator import validate_trajectory

    assert validate_trajectory(doc), "harbor rejected the trajectory"
    print("ok")


if __name__ == "__main__":
    _demo()

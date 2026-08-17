"""Baseline agent: a bash tool and a loop. Beat it.

This is deliberately naive. Everything obviously wrong with it is a place to
put your effort: it dumps raw command output straight into the context, it
truncates blindly at a byte count, it has no plan, no memory, no retry, and it
tells the model nothing about the environment it is standing in.

You may replace this file, this package, or the entire dependency list. The
fixed contract is small: the `agent` entrypoint declared in pyproject.toml, the
`--instruction` flag it is called with, and writing the conversation to
TRAJECTORY_PATH so your run shows up in the trajectory viewer. Keep `record()`
wired up if you rewrite the loop -- without it your traces are blank, and the
traces are how you work out what to fix.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from openai import OpenAI

SYSTEM_PROMPT = """You are a data engineer working inside a Linux container.

You have one tool: bash. Use it to inspect the environment, run the pipeline,
and edit files. Work step by step and verify your changes actually run.

When the task is complete, reply with a short summary and no tool call."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a bash command in the container and return its output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command to run."}
                },
                "required": ["command"],
            },
        },
    }
]

# ponytail: flat byte-truncation of tool output, which is exactly the naive
# thing to improve. Summarize, filter, or page instead.
MAX_OUTPUT_CHARS = 4000


def run_bash(command: str, timeout: int = 120) -> str:
    try:
        proc = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"(command timed out after {timeout}s)"

    output = (proc.stdout + proc.stderr).strip()
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n(output truncated)"
    return output or f"(no output, exit code {proc.returncode})"


def record(messages: list[dict]) -> None:
    """Write the conversation for the trajectory viewer.

    This harness is the only thing that knows what it actually sent, so it is
    the thing that says so. Rewritten every turn rather than once at the end,
    so a run that crashes or is killed on timeout still shows the turns it
    managed -- which are the runs worth looking at.
    """
    path = os.environ.get("TRAJECTORY_PATH")
    if not path:
        return

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"messages": messages, "tools": TOOLS}))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--model", default=os.environ.get("MODEL", "gemma"))
    parser.add_argument("--max-turns", type=int, default=40)
    args = parser.parse_args()

    client = OpenAI(
        base_url=os.environ["GATEWAY_URL"],
        api_key=os.environ.get("GATEWAY_API_KEY", "unused"),
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": args.instruction},
    ]

    for turn in range(args.max_turns):
        response = client.chat.completions.create(
            model=args.model,
            messages=messages,
            tools=TOOLS,
            # Gemma 4 on Bedrock cannot return more than one tool call per
            # turn. Asking for them anyway is an error, not a slow path.
            parallel_tool_calls=False,
        )
        message = response.choices[0].message
        messages.append(message.model_dump(exclude_none=True))
        record(messages)

        if not message.tool_calls:
            print(f"\n[turn {turn}] done: {message.content}", flush=True)
            return 0

        for tool_call in message.tool_calls:
            command = json.loads(tool_call.function.arguments).get("command", "")
            print(f"\n[turn {turn}] $ {command}", flush=True)
            result = run_bash(command)
            print(result, flush=True)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )
        record(messages)

    print(f"\nhit the {args.max_turns}-turn limit", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

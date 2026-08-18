"""Make tool-calling work for harnesses that ask for reasoning.

Gemma on Bedrock serves tools, and serves reasoning, but refuses both together
on /v1/chat/completions -- only /v1/responses allows the combination. Any
harness speaking the Anthropic API hits this: Claude Code always sends a
`thinking` block, LiteLLM turns that into `reasoning_effort` during
translation, and the request then arrives at the one endpoint that rejects it.

The model-level `reasoning_effort: "none"` in config.yaml cannot fix it,
because the translated value overwrites it. This runs later still -- after a
deployment is chosen, before the request goes out -- which is the first point
where the final parameters are visible.

Tools win over reasoning, because a data engineering agent that cannot run a
command is useless while one that cannot think out loud is merely worse.
"""

import re
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

# Measured against the deployed model, by binary search on a captured Claude Code
# request: 18 tools succeed, 19 return "Task submission failed ... Generation
# failed" with no mention of tools. Claude Code sends 28, so it fails on its
# first call, every time.
MAX_TOOLS = 18

# What a coding agent actually needs, and what merely travels with it. Claude
# Code's 28 include CronCreate, six Task* tools, EnterWorktree, DesignSync,
# SendMessage and a 21KB Workflow schema -- none of which mean anything inside a
# task container. Ordered by how much a rollout would miss them.
KEEP = re.compile(
    r"^(bash|shell|exec|run|read|view|cat|edit|str_replace|apply_patch|patch|write|create"
    r"|glob|grep|search|find|ls|list_files|notebook|todo|think|update_plan)",
    re.I,
)


def _name(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    fn = tool.get("function")
    return str((fn or {}).get("name") if isinstance(fn, dict) else tool.get("name") or "")


def _essential_first(tools: list) -> list:
    """The tools worth keeping when the model will not take them all.

    Truncating the list as sent is not enough: Claude Code's order is
    alphabetical, so the last ten are TaskCreate onwards -- but Write is 28th,
    and Write is the one to keep. Selection is by name, not position.
    """
    wanted = [t for t in tools if KEEP.match(_name(t))]
    rest = [t for t in tools if t not in wanted]
    return (wanted + rest)[:MAX_TOOLS]


class ToolsBeatReasoning(CustomLogger):
    async def async_pre_call_deployment_hook(
        self, kwargs: dict[str, Any], call_type: Any | None
    ) -> dict | None:
        if not kwargs.get("tools"):
            return None

        # `thinking` is the Anthropic spelling; it can survive translation.
        kwargs.pop("thinking", None)
        if kwargs.get("reasoning_effort") != "none":
            kwargs["reasoning_effort"] = "none"

        # This model returns one tool call per turn. Asking for several is not a
        # slow path, it is a 400 from the engine with no mention of which
        # parameter caused it.
        kwargs["parallel_tool_calls"] = False

        # Bedrock caps how many tools a request may carry, and answers a
        # request over the cap with the same unhelpful "Generation failed" as
        # everything else. Trim rather than fail: a harness with 28 tools works
        # with the 18 that matter, and one with fewer is untouched.
        if len(kwargs["tools"]) > MAX_TOOLS:
            before = kwargs["tools"]
            kwargs["tools"] = _essential_first(before)
            # Said out loud, because the harness's system prompt still describes
            # the dropped tools and the model may name them in text. Without
            # this line, "why does it keep mentioning TaskCreate" is a mystery;
            # with it, the answer is in the gateway log.
            dropped = [_name(t) for t in before if t not in kwargs["tools"]]
            print(
                f"trimmed tools {len(before)} -> {len(kwargs['tools'])}, "
                f"dropped: {', '.join(dropped)}",
                flush=True,
            )

        # Bedrock also rejects strict tool schemas.
        for tool in kwargs["tools"]:
            if isinstance(tool, dict):
                tool.pop("strict", None)
                fn = tool.get("function")
                if isinstance(fn, dict):
                    fn.pop("strict", None)
        return kwargs


proxy_handler_instance = ToolsBeatReasoning()

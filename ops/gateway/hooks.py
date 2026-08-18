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

# The most this model will accept, verified against the endpoint. The default it
# applies when a client names none is far smaller.
MAX_OUTPUT_TOKENS = 32000

# What the endpoint says it serves, quoted from its own refusal: "Supported tool
# types are: function, mcp, custom, namespace, tool_search." The hosted kinds --
# web_search, image_generation, code_interpreter -- would have to run on the
# provider's own infrastructure, and Bedrock serves the wire format rather than
# OpenAI's products. mcp stays in the list because a team may attach an MCP
# server through agent.yaml.
SERVED_TOOL_TYPES = (None, "function", "mcp", "custom", "namespace", "tool_search")

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

        # Only on chat completions. The refusal is specific to that endpoint --
        # /v1/responses serves tools and reasoning together, which is the whole
        # reason it exists -- and codex is the one harness that speaks it. Doing
        # this to a responses call takes away the reasoning the model needs to
        # produce a structured tool call, and it answers with prose describing
        # the call it did not make.
        on_responses = "responses" in str(getattr(call_type, "value", call_type))

        # A client that names no output limit gets the endpoint's own, which is
        # small enough to cut a response off mid tool call: codex sends none and
        # its stream ends with "reason: max_output_tokens", leaving the call it
        # was writing as prose. max_output_tokens is the responses spelling and
        # this model accepts it -- unlike max_tokens and max_completion_tokens,
        # which it rejects and config.yaml drops. Only filled in when absent, so
        # a harness that has an opinion keeps it.
        if on_responses and not kwargs.get("max_output_tokens"):
            kwargs["max_output_tokens"] = MAX_OUTPUT_TOKENS
            print(f"filled in max_output_tokens={MAX_OUTPUT_TOKENS}", flush=True)

        if not on_responses:
            # `thinking` is the Anthropic spelling; it can survive translation.
            kwargs.pop("thinking", None)
            if kwargs.get("reasoning_effort") != "none":
                kwargs["reasoning_effort"] = "none"

            # This model returns one tool call per turn on this endpoint. Asking
            # for several is not a slow path, it is a 400 from the engine with no
            # mention of which parameter caused it.
            kwargs["parallel_tool_calls"] = False

        # Bedrock serves function tools and nothing else: a request carrying a
        # built-in tool type is rejected whole, naming the type. codex ships a
        # `web_search` tool as standard, so every codex request was refused --
        # and through this proxy the refusal arrived as "Generation failed",
        # which is what made it take a day to find. Talking to Bedrock directly
        # says "Tool type 'web_search' is not supported" the first time.
        kept = [t for t in kwargs["tools"]
                if not isinstance(t, dict) or t.get("type") in SERVED_TOOL_TYPES]
        if len(kept) != len(kwargs["tools"]):
            dropped = [t.get("type") for t in kwargs["tools"] if t not in kept]
            print(f"dropped unsupported tool types: {dropped}", flush=True)
            kwargs["tools"] = kept
        if not kwargs["tools"]:
            kwargs.pop("tools", None)
            return kwargs

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

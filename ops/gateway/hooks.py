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

import json
import re
import uuid
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

# Measured against the deployed model, by binary search on a captured Claude Code
# request: 18 tools succeed, 19 return "Task submission failed ... Generation
# failed" with no mention of tools. Claude Code sends 28, so it fails on its
# first call, every time.
MAX_TOOLS = 18

# Enough for a tool call and an explanation, and no more. This is a budget, not
# a ceiling: measured against the endpoint, the same request answering with one
# function call took 10s at 4000 and 40s at 32000. Handing a harness the largest
# number the model accepts makes every turn four times slower, which inside a
# 300s agent budget is the difference between thirty turns and seven.
MAX_OUTPUT_TOKENS = 4000

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


# JSON Schema keywords the engine refuses inside a tool schema, at any depth. It
# answers with the same "Generation failed" it gives for everything else, so the
# only way to learn the list is one keyword at a time against the endpoint:
# propertyNames, not, if/then are rejected; oneOf, allOf, $ref/$defs,
# patternProperties, format and type-arrays all pass.
#
# This is what stopped Claude Code. Its schemas carry propertyNames (Artifact's
# capabilities map, AskUserQuestion's answers) and the request was refused
# whole, on the first turn, before a single tool ran -- which reads as a broken
# gateway rather than an unsupported schema. Bifrost fails this identically:
# the route is not what breaks the harness, the schema is.
#
# Dropped rather than rewritten: each of these only narrows what the model may
# send, so losing them costs validation the harness redoes on its own side.
UNSERVED_SCHEMA_KEYWORDS = ("propertyNames", "not", "if", "then", "else")


def _strip_unserved_schema(node: Any) -> None:
    """Remove the refused keywords in place, everywhere in a tool schema."""
    if isinstance(node, dict):
        for k in UNSERVED_SCHEMA_KEYWORDS:
            node.pop(k, None)
        for v in node.values():
            _strip_unserved_schema(v)
    elif isinstance(node, list):
        for v in node:
            _strip_unserved_schema(v)


# Appended to the instructions of every tools-carrying responses request. Short
# and imperative on purpose: the model that needs it is the one already losing
# the thread in a 9K-token system prompt.
TOOL_CHANNEL_NUDGE = (
    "\n\n# Tool calls\n"
    "Emit every tool call through the function-calling API. Never write a call "
    "as text: no <tool_code> blocks, no `name(arg=...)` in prose. Never write a "
    "<tool_response> yourself -- the result of a call is given to you, and text "
    "that describes a call is not a call and will not run."
)


# Gemma writes its tool calls three ways when it falls back to text, all of them
# inside a <tool_code> block:
#
#     exec_command(cmd="echo hi > /app/f")
#     exec_command(cmd='echo hi > /app/f')
#     exec_command { cmd: "echo hi > /app/f" }
#
# Name first, then every key: "value" or key = "value" pair inside. Deliberately
# narrow: a block this does not understand is left alone and the turn fails the
# way it does today, which is the safe direction -- the alternative is inventing
# a call the model did not make and running it.
TOOL_CODE = re.compile(r"<tool_code>\s*(.*?)\s*</tool_code>", re.S)
_CALL_NAME = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*[({]")
_CALL_ARG = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(\"(?:[^\"\\\\]|\\\\.)*\"|'(?:[^'\\\\]|\\\\.)*')")


def _parse_tool_code(text: str, declared: set[str]) -> tuple[str, dict] | None:
    """The first <tool_code> call in `text`, if it names a tool that was sent.

    The declared-name check is the whole safety story: the model also forges
    <tool_response> blocks and narrates work it never did, and only a name the
    harness actually offered can be handed back to it as a real call.
    """
    for block in TOOL_CODE.findall(text or ""):
        m = _CALL_NAME.match(block)
        if not m or m.group(1) not in declared:
            continue
        args = {}
        for key, raw in _CALL_ARG.findall(block):
            try:
                args[key] = json.loads(raw) if raw[0] == '"' else raw[1:-1]
            except ValueError:
                return None
        if args:
            return m.group(1), args
    return None


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

        # Gemma answers a long agent prompt in its own native tool-call syntax:
        # a `<tool_code>exec_command(cmd="...")</tool_code>` block in ordinary
        # assistant text, usually followed by a `<tool_response>` it invents for
        # itself and a report of work it never did. Nothing downstream parses
        # that, so the harness sees a turn with no call, runs nothing, and the
        # model congratulates itself. It is the same failure the community hits
        # on every server without a Gemma tool-call parser
        # (ml-explore/mlx-lm#1096), and it is what stops codex here: the same
        # endpoint returns structured calls for a short prompt and one tool.
        #
        # Asked for rather than parsed out afterwards. Re-parsing means reading
        # a command out of prose and running it, including the ones the model
        # forged a result for -- a wrong guess there executes something nobody
        # asked for. A sentence of instruction cannot do that, and if the model
        # ignores it the turn fails exactly as it does today.
        if on_responses:
            instr = kwargs.get("instructions")
            if isinstance(instr, str) and TOOL_CHANNEL_NUDGE not in instr:
                kwargs["instructions"] = instr.rstrip() + TOOL_CHANNEL_NUDGE
                print("appended tool-channel instruction", flush=True)

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
                _strip_unserved_schema(tool)
        return kwargs



    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        """Re-emit a text tool call as a real one, on the responses route.

        Measured 2026-08-19: with a short prompt and one tool this endpoint
        returns a structured call every time, but under codex's ~9K-token system
        prompt Gemma answers in its own <tool_code> syntax instead -- and then
        invents the <tool_response> and reports the work as done. The harness
        sees a turn with no call, runs nothing, and repeats. Asking it not to
        (see TOOL_CHANNEL_NUDGE) moved 0/6 to 2/9, which is not a fix.

        Only what the harness already offered is re-emitted, and only on a
        response that made no real call.
        """
        output = getattr(response, "output", None)
        if not isinstance(output, list) or not data.get("tools"):
            return response
        if any(getattr(o, "type", None) == "function_call" for o in output):
            return response

        declared = {_name(t) for t in data["tools"]} - {""}
        for item in output:
            for part in getattr(item, "content", None) or []:
                found = _parse_tool_code(getattr(part, "text", "") or "", declared)
                if not found:
                    continue
                name, args = found
                output.append({
                    "type": "function_call",
                    "id": f"fc_{uuid.uuid4().hex}",
                    "call_id": f"call_{uuid.uuid4().hex}",
                    "name": name,
                    "arguments": json.dumps(args),
                    "status": "completed",
                })
                print(f"recovered text tool call: {name}", flush=True)
                return response
        return response


proxy_handler_instance = ToolsBeatReasoning()


if __name__ == "__main__":
    # The two pieces here with a shape they can get wrong: the schema walk has
    # to reach keywords nested under properties, arrays and $defs and leave the
    # rest alone, and the tool-call parser has to refuse what it should not
    # recover. Run inside the gateway container, which is where litellm is:
    #   docker compose -f ops/gateway/docker-compose.yml exec gateway python /app/hooks.py
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "object", "propertyNames": {"type": "string"}},
            "b": {"type": "array", "items": {"not": {"type": "number"}}},
        },
        "if": {"required": ["a"]},
        "then": {"required": ["b"]},
        "oneOf": [{"required": ["a"]}],
    }
    # The three <tool_code> spellings, plus the two that must not be recovered:
    # a tool the harness never offered, and a block whose arguments do not parse.
    declared = {"exec_command"}
    for block, want in [
        ('<tool_code>exec_command(cmd="echo hi > /app/f")</tool_code>', {"cmd": "echo hi > /app/f"}),
        ("<tool_code>exec_command(cmd='echo hi')</tool_code>", {"cmd": "echo hi"}),
        ('<tool_code>exec_command { cmd: "echo hi" }</tool_code>', {"cmd": "echo hi"}),
    ]:
        assert _parse_tool_code(block, declared) == ("exec_command", want), block
    assert _parse_tool_code('<tool_code>rm_rf(path="/")</tool_code>', declared) is None
    assert _parse_tool_code("exec_command(cmd=\"echo hi\")", declared) is None
    # A forged result alongside the call must not stop the call being recovered.
    forged = ('<tool_code>exec_command(cmd="ls")</tool_code>\n'
              '<tool_response>{"stdout": "a b c"}</tool_response>')
    assert _parse_tool_code(forged, declared) == ("exec_command", {"cmd": "ls"})

    _strip_unserved_schema(schema)
    assert schema == {
        "type": "object",
        "properties": {"a": {"type": "object"}, "b": {"type": "array", "items": {}}},
        "oneOf": [{"required": ["a"]}],
    }, schema
    print("ok")

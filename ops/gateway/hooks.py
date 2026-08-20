"""Fix up requests this endpoint would refuse, on their way out.

Three things, all of them measured against the deployed model rather than
inferred, and each one refused with the same unhelpful "Generation failed":

  * JSON Schema keywords it does not serve, anywhere in a tool schema.
  * Tool types it does not serve -- codex ships a `web_search` tool as standard.
  * A missing output limit on the responses route, which cuts a reply off mid
    tool call.

This runs after a deployment is chosen and before the request goes out, which
is the first point where the final parameters are visible.

What used to be here and is not any more, because both were measured wrong the
first time and re-measured on 2026-08-19:

  * A cap of 18 tools. Found by binary search on captured Claude Code requests,
    every one of which also carried a `propertyNames` schema -- so the search
    was measuring the schema refusal, not a tool limit, and landed on a
    boundary that meant nothing. 60 tools and 48KB of clean schemas pass.
  * Stripping reasoning from tool-calling chat completions, on the grounds that
    this endpoint refuses the combination. It does not: with tools and
    `reasoning_effort: "high"` together it returns reasoning content and a
    correct tool call, three times out of three.

Reasoning is still off by default, but in one place now rather than two --
`reasoning_effort` in config.yaml. Turning it on is a live question, not a
constraint.
"""

import uuid
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

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


def _unique_call_id() -> str:
    """A tool call id no other call in the conversation will have."""
    return f"call_{uuid.uuid4().hex[:16]}"


def _rewrite_tool_call_ids(message: Any) -> int:
    """Give every tool call on a message its own id. Returns how many changed.

    The engine numbers tool calls by their position in the response, so a model
    that makes one call per turn returns `call_0` every single turn. Nothing
    downstream can tell those apart.
    """
    calls = getattr(message, "tool_calls", None)
    if not calls and isinstance(message, dict):
        calls = message.get("tool_calls")
    if not calls:
        return 0
    n = 0
    for call in calls:
        if isinstance(call, dict):
            if call.get("id") is not None:
                call["id"] = _unique_call_id()
                n += 1
        elif getattr(call, "id", None) is not None:
            call.id = _unique_call_id()
            n += 1
    return n


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


class ServedRequestsOnly(CustomLogger):
    async def async_pre_call_deployment_hook(
        self, kwargs: dict[str, Any], call_type: Any | None
    ) -> dict | None:
        if not kwargs.get("tools"):
            return None

        # The two routes differ in what they need filled in and what they
        # refuse, so everything below is one or the other.
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
            # This model returns one tool call per turn on this endpoint. Asking
            # for several is not a slow path, it is a 400 from the engine with no
            # mention of which parameter caused it.
            kwargs["parallel_tool_calls"] = False

        # Thinking and tools together, on the Anthropic route, produce turns
        # that are thinking and nothing else: no tool call, no text. The
        # harness has nothing to act on, records "(no content)" and asks again,
        # and the rollout spends its whole budget that way.
        #
        # Measured 2026-08-20 by capturing what Claude Code actually sends: one
        # request carried 103 thinking blocks, 105 "(no content)" turns and a
        # single tool call. It asks for `{"thinking": {"type": "adaptive"}}`
        # and `output_config.effort = "high"`, and sends them whatever
        # CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING is set to -- that variable does
        # not reach this request.
        #
        # This used to be dropped in config.yaml and I removed it earlier the
        # same day, having measured tools and reasoning coexisting happily. That
        # measurement was on chat completions with `reasoning_effort`, which is
        # a different route and a different parameter, and it did not test the
        # one that breaks. The canary did not catch the regression either: its
        # task is answerable in a single turn, so it never needs a second one.
        for param in ("thinking", "output_config"):
            if kwargs.pop(param, None) is not None:
                print(f"dropped {param} from a tool-calling request", flush=True)

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

        # Bedrock also rejects strict tool schemas.
        for tool in kwargs["tools"]:
            if isinstance(tool, dict):
                tool.pop("strict", None)
                fn = tool.get("function")
                if isinstance(fn, dict):
                    fn.pop("strict", None)
                _strip_unserved_schema(tool)
        return kwargs

    async def async_post_call_success_hook(
        self, data: dict, user_api_key_dict: Any, response: Any
    ) -> Any:
        """Make every tool call id unique before the harness sees it.

        The engine ids tool calls by position within one response, so a model
        that makes a single call per turn hands back `call_0` on every turn of
        the conversation. The OpenAI-shaped harnesses survive it, because a
        tool result there follows the call it belongs to and position is enough
        to pair them. The Anthropic message format threads strictly by id, so a
        conversation where every `tool_use` and every `tool_result` says
        `call_0` cannot be threaded at all -- and the results are dropped.

        That is what made claude-code unusable: it read a file, the result was
        discarded on the way back in, and it read the same file again, forty
        times, until the agent timeout. It reads as a model too weak to hold a
        plan and it is nothing of the sort. Measured 2026-08-20: 76 tool calls
        in one rollout, every one of them `call_0`.
        """
        changed = 0
        for choice in getattr(response, "choices", None) or []:
            message = getattr(choice, "message", None)
            if message is not None:
                changed += _rewrite_tool_call_ids(message)
        if changed:
            print(f"made {changed} tool call id(s) unique", flush=True)
        return response

    async def async_post_call_streaming_iterator_hook(
        self, user_api_key_dict: Any, response: Any, request_data: dict
    ) -> Any:
        """The same, for a streamed response.

        An id arrives once, on the first delta of each call, and later chunks
        for that call carry only the arguments -- so rewriting whenever an id
        is present is enough, and there is nothing to keep between chunks.
        """
        async for chunk in response:
            for choice in getattr(chunk, "choices", None) or []:
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    _rewrite_tool_call_ids(delta)
            yield chunk


proxy_handler_instance = ServedRequestsOnly()


if __name__ == "__main__":
    # The one piece here with a shape it can get wrong: the walk has to reach
    # keywords nested under properties, arrays and $defs, and leave the rest
    # alone. Run inside the gateway container, which is where litellm is:
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
    _strip_unserved_schema(schema)
    assert schema == {
        "type": "object",
        "properties": {"a": {"type": "object"}, "b": {"type": "array", "items": {}}},
        "oneOf": [{"required": ["a"]}],
    }, schema
    # The other piece with a shape it can get wrong: ids have to change, and
    # they have to differ from each other.
    class _Call:
        def __init__(self, id): self.id = id

    class _Msg:
        def __init__(self): self.tool_calls = [_Call("call_0"), _Call("call_0")]

    m = _Msg()
    assert _rewrite_tool_call_ids(m) == 2
    assert m.tool_calls[0].id != m.tool_calls[1].id, "ids collided"
    assert all(c.id != "call_0" for c in m.tool_calls)

    d = {"tool_calls": [{"id": "call_0", "function": {"name": "bash"}}]}
    assert _rewrite_tool_call_ids(d) == 1
    assert d["tool_calls"][0]["id"] != "call_0"
    assert _rewrite_tool_call_ids({"tool_calls": []}) == 0
    assert _rewrite_tool_call_ids({}) == 0

    print("ok")

# Harnesses against this gateway

Everything learned, per harness, about running off-the-shelf coding agents
against Gemma 4 31B through the gateway. Every line here was paid for with a
bench run; read it before choosing a harness or debugging one.

Whoever runs the event has a canary that exercises every harness below against
a trivial task; ask them if you suspect a harness rather than your own work.

## What the gateway does to every request

The model (Bedrock mantle, `google.gemma-4-31b`) is not a standard OpenAI
endpoint, and the gateway absorbs the difference so harnesses don't have to
(in the gateway configuration, which the instructors hold):

| Request feature | What happens | Why |
|---|---|---|
| `max_tokens` / `max_completion_tokens` | Dropped | The model rejects both as unsupported; Claude Code and the Anthropic API always send one |
| `propertyNames`, `not`, `if`/`then` in a tool schema | Stripped, at any depth | Refused by the endpoint with an opaque "Generation failed". This is what stopped Claude Code entirely until 2026-08-19 |
| Built-in tool types (`web_search`, …) | Dropped | Bedrock serves `function`, `mcp`, `custom`, `namespace`, `tool_search` and nothing else |
| Parallel tool calls | Forced off (chat completions) | The model returns one tool call per turn; asking for more is a 400 |
| No `max_output_tokens` on `/v1/responses` | Filled in (4000) | The endpoint's own default cuts a reply off mid tool call |
| `strict` tool schemas | Stripped | Rejected by Bedrock |
| Unknown params | Dropped (`drop_params: true`) | A harness sending something exotic gets served, not 400'd |

**Reasoning is not touched.** It used to be forced off and `thinking` dropped,
on the measured claim that this endpoint refuses tools and reasoning together.
Re-measured 2026-08-19: with tools and `reasoning_effort: "high"` it returns
reasoning content *and* a correct tool call, three times out of three, and
tools work with no reasoning parameter at all. Both workarounds are gone and
the choice belongs to the harness.

**There is no tool-count cap either.** One used to trim every request to 18
tools by name; the binary search behind it ran on requests that all carried a
`propertyNames` schema, so it was measuring the refusal above. 60 tools and
48KB of clean schemas pass.

Also true of the model: one tool call per turn, 256K context, reasoning content
only on `/v1/responses`, no websocket/realtime routes. And on `/v1/responses`
specifically, a long agent prompt makes it write tool calls as text rather than
emit them — see "Tried and not supported".

## Per harness

Five harnesses are supported: **opencode**, **pi**, **claude-code**,
**trae-agent** and the **custom** `agent/` baseline. Everything else that was tried is below, with why
it failed, so nobody spends the day rediscovering it.

Calibration figures -- which harness scores what, and which scored tasks each
one fails -- are deliberately not here. They named eight of the scored tasks
and said which were hardest, which is a map of where the points are for anyone
reading this repo. They live in the private task repository with the rest of
the difficulty analysis.

What is safe to say: the suite is hard on purpose. The strongest harness
measured solves well under half of it, several tasks are unsolved by every
harness tried, and a score in the single digits is the expected shape rather
than a sign something is broken. Start with the five samples in `tasks/` before
spending a submission.

aider is not supported despite scoring 11/21 on an earlier bench: it edits
files from its repo map and cannot run a command, so it answers a
data-engineering task without ever looking at the data. Task images still
commit their fixtures, which is what made that repo map work and is worth
keeping for any harness that navigates by reading.

### opencode

- `model_name: openai/gemma`, reads `OPENAI_BASE_URL`/`OPENAI_API_KEY` inside
  the task container.
- The prefix is not optional and was not always required: harbor's adapter now
  raises `Model name must be in the format provider/model_name` before its
  first model call (`opencode.py:481`). A plain `gemma` had been correct, so
  this broke on a harbor upgrade rather than on anything here -- worth checking
  after any harbor bump, for every harness listed in this file.

### pi

- `model_name: gateway/gemma` — harbor's adapter splits it into
  `--provider gateway --model gemma`.
- Ignores `OPENAI_BASE_URL` entirely (tested: still authenticates against
  api.openai.com and 401s). Its only supported route is a custom provider in
  `~/.pi/agent/models.json`, which the base image ships, pointed at the
  deployed gateway.
- That file is written with `printf`, not a Dockerfile heredoc: heredocs are a
  BuildKit feature, and the legacy builder writes a zero-byte file and reports
  success — pi then says `Unknown provider "gateway"` while every log says the
  image is ready.
- Resolves its key in the worker's process, not the container.

### claude-code

- `model_name: gemma`. Speaks the Anthropic API: wants `ANTHROPIC_BASE_URL`
  **without** `/v1` (it appends `/v1/messages` itself; handed the OpenAI-style
  base it posts to `/v1/v1/messages` and reports the model as missing).
- Sends a `thinking` block and 24 tools, and both travel now. The gateway used
  to strip the reasoning and trim the tools to 18; neither was necessary, and
  the story of why is worth one paragraph because it is the trap this whole
  file exists to prevent.
- **It works, after two bugs in this gateway were fixed.** It scored 0/17 on
  the 2026-08-20 graded run, burning 2.45M tokens per task against opencode's
  307K and running to the 300s cap every time. Read that as a measurement of
  the gateway, not of the harness: with both fixes in it solves a scored task
  end to end -- 38 turns, `stop_reason: end_turn`, zero interruptions, reward 1
  -- running the pipeline, querying the warehouse to check its own output, then
  editing the config and writing a new model.

  *The first bug was `thinking` and `output_config` on tool-calling requests.*
  The model answers with reasoning and no tool call, the harness has nothing to
  act on, writes `(no content)` and asks again: one captured request carried
  103 thinking blocks, 105 `(no content)` turns and a single tool call. Claude
  Code sends `{"thinking": {"type": "adaptive"}}` and
  `output_config.effort: high` whatever `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING`
  says -- that variable never reaches the request. `output_config` is also
  [an open LiteLLM bug](https://github.com/BerriAI/litellm/issues/22963). Both
  are dropped in hooks.py.

  This one was self-inflicted: the gateway used to drop them and stopped on
  2026-08-19, on a measurement of tools and `reasoning_effort` coexisting on
  chat completions -- a different route and a different parameter from the one
  that breaks.

  *The second was that every tool call came back as `call_0`.* The id is the
  call's index within a response and this model makes one call per turn, so it
  is index 0 forever -- 76 identical ids in one rollout. Claude Code aborts a
  `tool_use` whose id it has already handled, so the first call runs and the
  rest are discarded; that is what `[Tool use interrupted]` means in its
  transcript. The id comes from the endpoint, not LiteLLM, whose source has no
  `call_` prefix anywhere.

  It is fixed in a small proxy in front of the gateway rather than in a
  LiteLLM hook,
  because LiteLLM runs no post-call hook on `/v1/messages` -- their
  [issue #27518](https://github.com/BerriAI/litellm/issues/27518), and measured
  here as 352 pre-call hook events against 3 post-call over forty minutes. The
  proxy runs in the gateway container, litellm moves to 4001, and terraform
  does not change.

  **It rewrites `/v1/messages` and relays every other route byte for byte.**
  The OpenAI-shaped harnesses pair a tool result with the call before it, so a
  repeated id costs them nothing and all four score the same with `call_0`
  throughout. They should not carry a change they do not need.
  `TOOL_ID_PROXY_ENABLED=0` disables the rewrite without a rebuild.

  Prior art agrees on the first fix and goes further:
  [claude-code-local](https://github.com/nicedreamzapp/claude-code-local) runs
  Claude Code against Gemma 4 31B and ships a thinking filter on by default,
  recovery for garbled tool JSON, and a 28x reduction of the harness prompt.
  People do run this pairing; nobody does it through stock LiteLLM.

  Note what could not have caught any of this. The canary answers in one turn,
  so it never makes a second tool call -- which is where every one of these
  failures begins. It passed throughout. It proves a harness can reach the
  model and nothing more.

- It did not work at all until 2026-08-19, and that was a separate fault: every
  request was refused whole. Its schemas carry `propertyNames`, which this
  endpoint rejects at any depth, and the refusal arrives as an opaque
  `Generation failed` -- so a harness arguing with a 400 read as one that could
  not stay on task for 168 turns.

  I concluded from that fix that the old "42 turns, 360k tokens, most fragile
  harness" reputation was an artefact of the schema bug and should be treated
  as unmeasured. The graded run says otherwise: the schema bug is fixed and it
  still churns to the cap. Two faults, and fixing the loud one did not touch
  the quiet one.
- The two gateway workarounds were both artefacts of that bug. The 18-tool cap
  came from a binary search over captured Claude Code requests, every one of
  which carried `propertyNames`, so it measured the schema refusal and stopped
  at a meaningless boundary; 60 tools and 48KB of clean schemas pass. The
  reasoning strip assumed this endpoint refuses tools and reasoning together,
  which it does not. Both are gone, and Claude Code passes the canary with all
  24 tools and its thinking intact.
- Routing it through a different gateway does not help: a gateway cannot route
  around a schema the model's endpoint refuses. Measured against Bifrost the
  same day -- identical request, identical refusal.

### trae-agent

- `model_name: openai/gemma`. Validates the provider prefix the way opencode
  does (`trae_agent.py:154`), and reads `OPENAI_BASE_URL` inside the container.
- Nothing else needed: no provider file, no baked install, no environment
  spelling of its own. Added 2026-08-20 after passing the canary first try,
  making real tool calls -- three `bash`, three `str_replace_based_edit_tool`,
  two `task_done`.
- Useful as the counter-example to codex: a full agent harness with a long
  prompt *can* drive this model, as long as it speaks chat completions. The
  tool-call failure is specific to `/v1/responses`, not general.

### custom (the `agent/` baseline)

- `import_path: harness.submission_agent:Submission`, `model_name: gemma`.
- Reads `GATEWAY_URL`/`GATEWAY_API_KEY`. Runs in `/app` (the task workspace)
  with uv pointed at the submission checkout — starting in the checkout instead
  once made an agent write its correct answer next to its own pyproject.toml
  while the verifier read the untouched stub.

## Tried and not supported

Four harnesses were taken as far as they go against this model on 2026-08-19.
None of them is here because of a missing setting, and the first three fail on
one thing: **Gemma stops emitting structured tool calls under a long agent
prompt and writes them as text instead.** With a short prompt and one tool the
same endpoint returns a structured call every time (12/12, streaming and not,
at every reasoning level); under codex's ~9K-token system prompt it answers in
its own native `<tool_code>` syntax, then invents a `<tool_response>` for
itself and reports work it never did. This is a known Gemma problem wherever a
server has no parser for its native format
([ml-explore/mlx-lm#1096](https://github.com/ml-explore/mlx-lm/issues/1096)).

**codex.** Ruled out, in order: pinning to 0.116 per
[openai/codex#19871](https://github.com/openai/codex/issues/19871) (fails
identically), `wire_api = "chat"` (removed upstream, refuses to start),
instructing the model not to do it (2/9 against a 0/6 baseline), and recovering
the call in the gateway. The last one is the interesting failure: the gateway
*can* parse the text call and re-emit it as a real one, on the stream and in
the completed response, and codex discards it anyway. A gateway cannot fix this
from outside the harness.

**mini-swe-agent.** Same cause, its own error message: `No tool calls found in
the response. Every response MUST include at least one tool call`, then
`RepeatedFormatError` after it re-explains the protocol and gets prose again.

**swe-agent.** Two harness bugs fixed before reaching the same wall, both worth
knowing if it is ever revisited. Harbor's adapter passes the literal string
`$(pwd)` as the repo path -- single-quoted inside an `echo`, so it is never
expanded -- and swe-agent dies in `check_valid_repo` on `/app/$(pwd)`; a
`/testbed` symlink takes the adapter's other branch. Then it asks litellm
whether the model supports function calling, gets `False` for a name litellm
has never heard of, and parses replies as tool calls anyway, submitting empty
patches. `SWEAGENT_CONFIG=/opt/sweagent-configs/default_backticks.yaml` is
upstream's matched parser-and-templates pair for prose commands; overriding
only `parse_function` renders an empty user message, because the two are a
pair. With both fixed it reaches the model and fails in its parser.

**qwen-coder.** Refused upstream on its first call -- the same opaque
`Generation failed` that hid the schema bug -- and its trajectory does not
record the tools it sent, so the cause is unidentified. Worth another look only
if someone wants it specifically.

**openhands.** Not the model -- it cannot be installed. Harbor verifies with
`python -m openhands.core.main --version`; that module is gone from
`openhands-ai` 1.x (the package is `openhands.sdk` now), and 0.47, 0.48 and
0.49 all fail it with `No module named 'deprecated'`, a dependency those
releases do not declare. `uv pip install openhands-ai==0.49.0 Deprecated`
works, which the adapter has no way to express.

Three more cannot reach this model at all, by construction: **copilot-cli**
authenticates to GitHub and picks from Copilot's catalogue, **gemini-cli**
takes only Google's credentials, and **Kiro** is an AWS service with a fixed
model roster and no adapter. None has a base URL to override, so none of them
can run the model everyone else is running.

## Environment a rollout gets

The worker passes the gateway in every spelling anyone reads, both into the
task container (`--ae`) and into the harbor process itself (pi resolves its
key there): `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `ANTHROPIC_BASE_URL`
(no `/v1`), `ANTHROPIC_API_KEY`, `GATEWAY_URL`, `GATEWAY_API_KEY`. `just eval`
and the graded runner set the same, which is what keeps local runs and graded
runs the same experiment.

## Adding a harness

1. Check Harbor ships an adapter for it (`aider, codex, copilot-cli,
   cursor-cli, gemini-cli, goose, hermes, kimi-cli, langgraph, opencode,
   openhands, pi, qwen-code, swe-agent, terminus-2, trae-agent`).
2. Find the spelling and endpoint route it needs (this file tells you where the
   traps are: env vars vs. provider files, prefix-eating adapters).
3. If it needs a provider file, ship it in `tasks/base/Dockerfile` with
   `printf`, and parse it in the same layer so a malformed file fails the build.
4. Ask whoever runs the event to add it to the canary.
5. Record what you learned here.

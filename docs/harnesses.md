# Harnesses against this gateway

Everything learned, per harness, about running off-the-shelf coding agents
against Gemma 4 31B through the gateway. Every line here was paid for with a
bench run; read it before choosing a harness or debugging one.

Quick sanity check for all of them: `just ops::canary` runs every harness below
against a trivial task and says which ones are broken.

## What the gateway does to every request

The model (Bedrock mantle, `google.gemma-4-31b`) is not a standard OpenAI
endpoint, and the gateway absorbs the difference so harnesses don't have to
(`ops/gateway/config.yaml`, `ops/gateway/hooks.py`):

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

Suite scores, measured 2026-08-18 on the 21-task suite that existed then:
**pi 14/21, opencode 6/21, aider 2/21, codex 0/21**. The suite is now sixteen
scored tasks, the five easiest having become unscored public samples.

**pi scores 10/16 on the current suite** (2026-08-20, one trial per task,
`a-scored-task` unmeasured -- a local disk-full, not a task defect). The design
target is ~30%, so it is still roughly twice as easy as intended, and the two
changes made that day did not move it:

* *Held-out grader fixtures* -- five graders now also run against input in
  /tests that the agent never sees. This closes a real hole (an answer fitted
  to the visible sample used to pass) but costs a competent harness nothing:
  pi passed `a-scored-task` with all six new edge cases.
* *De-specified instructions* -- the output contract moved from instruction.md
  into the workspace stub. Two tasks pi previously failed started passing
  (`a-scored-task`, `a-scored-task`), which is the
  wrong direction; a contract in the file the agent is already reading may
  simply survive the context better than one in a long prompt.

Do not read a one-trial difference as a result. pi's two graded submissions
were 14/21 and 0/21, so the noise floor swamps a 9-vs-10 change; calibration
needs n>=3 per task. What the failures do say is structural: pi fails
`a-scored-task`, `proxmox`, `a-scored-task`, `a-scored-task` and
`a-scored-task`, every one of which needs several correct actions in sequence or
a running system to interact with, and passes nearly everything that is one
file solved in one shot. Difficulty lives on that axis, and no prompt edit
reaches it -- it takes authoring multi-step tasks.

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
- **It scores 0/17 and burns eight times the tokens doing it.** Measured on the
  graded fleet, 2026-08-20: 2.45M tokens per task against opencode's 307K and
  pi's 285K, and 351s per task against their 115s -- which is the 300s agent
  cap plus the verifier, on every single task. It does not finish and get the
  answer wrong; it never stops, and the timeout ends it.

  **The cause is Claude Code's own de-duplication guard.** Reproduced locally
  on `a-scored-task`: one `ls`, then the same `Read` of the same
  file forty times. The first read returns the file. Every one after it returns

      Wasted call -- file unchanged since your last Read.
      Refer to that earlier tool_result instead.

  which Claude Code generates itself, to stop a capable model wasting turns.
  Gemma does not act on it. It reads the message as a failed call and reissues
  the identical read, gets scolded again, and repeats until the timeout. By
  turn 24 it is leaking fragments of its own reasoning into the arguments --
  `file_path: "analyze_trajectory.py destroy"`.

  Two things follow. **The gateway cannot fix this**: the guard is produced
  inside the harness and the repeated reads never reach the gateway, which sees
  forty ordinary successful requests and logs no errors at all. And **the
  canary cannot catch it**: its task is small enough to finish before anything
  needs a second look, which is why claude-code passes the canary and fails
  every real task.

  A team that picks this harness spends its budget getting nothing, with no
  signal that the harness rather than its own context engineering is at fault.
  Warn them, or do not offer it.
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
and `just ops::canary` set the same, which is what keeps local runs and graded
runs the same experiment.

## Adding a harness

1. Check Harbor ships an adapter for it (`aider, codex, copilot-cli,
   cursor-cli, gemini-cli, goose, hermes, kimi-cli, langgraph, opencode,
   openhands, pi, qwen-code, swe-agent, terminus-2, trae-agent`).
2. Find the spelling and endpoint route it needs (this file tells you where the
   traps are: env vars vs. provider files, prefix-eating adapters).
3. If it needs a provider file, ship it in `tasks/base/Dockerfile` with
   `printf`, and parse it in the same layer so a malformed file fails the build.
4. Add it to `ops/canary/agents.yaml` and run `just ops::canary`.
5. Record what you learned here.

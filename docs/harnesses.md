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
| Reasoning + tools together | Reasoning forced off (`reasoning_effort: "none"`) | Chat Completions refuses the combination; tools win because an agent that cannot run a command is useless |
| `thinking` (Anthropic) | Dropped | Becomes `reasoning_effort` in translation, then hits the rule above |
| Parallel tool calls | Forced off | The model returns one tool call per turn; asking for more is a 400 |
| More than 18 tools | Trimmed to the 18 most essential, **by name** (bash/read/edit/write/grep first) | The model refuses the 19th tool with an opaque "Generation failed"; Claude Code sends 28 |
| `strict` tool schemas | Stripped | Rejected by Bedrock |
| Unknown params | Dropped (`drop_params: true`) | A harness sending something exotic gets served, not 400'd |

The tool trim is logged by the gateway (`trimmed tools 28 -> 18, dropped: ...`),
so if a model rambles about a tool it never calls, check the gateway log: the
harness's system prompt still describes tools the model was never given.

Also true of the model: one tool call per turn, 256K context, reasoning content
only on `/v1/responses`, no websocket/realtime routes.

## Per harness

Suite scores from the calibration benches (21 tasks): **aider 11/21,
opencode 8/21, codex 4/21**.

### aider

- `model_name: openai/openai/gemma` — the only spelling that works. Harbor's
  adapter eats the first prefix, and aider itself needs one to pick a provider.
- Reads `OPENAI_BASE_URL`/`OPENAI_API_KEY`, resolved in the *worker's* process
  while the command is built, not (only) in the task container.
- Navigates by repo map, and the repo map comes from git: a workspace without a
  git repo makes aider ask the user to paste code. Every task image commits its
  fixtures for this reason.

### opencode

- `model_name: gemma`, reads `OPENAI_BASE_URL`/`OPENAI_API_KEY` inside the task
  container.
- No known quirks beyond the gateway-absorbed ones.

### codex

- `model_name: gemma`, but env vars are not enough: codex ignores
  `OPENAI_BASE_URL` unless a provider in `~/.codex/config.toml` declares it.
  The base image (`tasks/base/Dockerfile`) ships that provider, pointed at the
  deployed gateway, with `wire_api = "responses"` (the chat wire was removed
  upstream).
- Sandboxes commands with bubblewrap, which needs user namespaces a task
  container doesn't have — the base image sets
  `sandbox_mode = "danger-full-access"` or every command fails and codex asks
  the user for help (which once read as 21 tasks of zeros).
- Tries a websocket first (`/v1/responses` upgrade, then `/v1/realtime`);
  LiteLLM serves neither. It falls back to HTTPS after five attempts, ~10s per
  rollout. Harmless, but it means the rollout's key must outlive the container:
  retiring the key before teardown produced "Invalid proxy server token" on
  twelve of twenty-one rollouts.

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
- Like aider, resolves its key in the worker's process, not the container.

### claude-code

- `model_name: gemma`. Speaks the Anthropic API: wants `ANTHROPIC_BASE_URL`
  **without** `/v1` (it appends `/v1/messages` itself; handed the OpenAI-style
  base it posts to `/v1/v1/messages` and reports the model as missing).
- Always sends a `thinking` block and 28 tools; both are handled by the gateway
  (reasoning stripped, tools trimmed to 18). Runs are set with
  `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1` as belt and braces.
- With the trim in place it runs (measured: 42 turns, 360k input tokens, every
  request 200), but it is the most fragile harness against this model. Try it;
  do not plan your afternoon around it.

### submission (the `agent/` baseline)

- `import_path: harness.submission_agent:Submission`, `model_name: gemma`.
- Reads `GATEWAY_URL`/`GATEWAY_API_KEY`. Runs in `/app` (the task workspace)
  with uv pointed at the submission checkout — starting in the checkout instead
  once made an agent write its correct answer next to its own pyproject.toml
  while the verifier read the untouched stub.

## Environment a rollout gets

The worker passes the gateway in every spelling anyone reads, both into the
task container (`--ae`) and into the harbor process itself (aider and pi
resolve keys there): `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `ANTHROPIC_BASE_URL`
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

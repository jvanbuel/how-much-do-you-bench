# The shared base for every task image.
#
# The harness block is ~1GB. Docker shares a layer only when the whole parent
# chain matches, so putting it in each task's own Dockerfile stored it once per
# task -- six tasks, six copies. Here it is stored once and every task inherits
# it.
#
# Built and published by `just ops::base-image`. Pinned by tag so a task image
# cannot silently change underneath a calibration.
FROM python:3.12-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NVM_DIR=/root/.nvm \
    UV_LINK_MODE=copy \
    PATH="/root/.local/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         curl ca-certificates git unzip jq \
         build-essential tmux procps \
    && rm -rf /var/lib/apt/lists/*

# uv, for the task's own dependencies and for the pip-installed harnesses.
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /usr/local/bin/

# The harnesses. Trimmed to those that can talk to an OpenAI-compatible
# endpoint, which is what the gateway serves: gemini-cli and qwen-code target
# their own providers, and copilot-cli authenticates to GitHub rather than to a
# base URL, so none of the three can be pointed at this model at all.
# claude-code speaks the Anthropic API, which the gateway also serves, and
# harbor installs it on demand. Harbor installs any other harness on demand too
# -- this only decides which ones are already warm.
#
# Versions track harbor.agents.installed.node_install, so `nvm install 22` and
# `npm i -g` become checks rather than downloads.
RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash \
    && . "$NVM_DIR/nvm.sh" \
    && nvm install 22 \
    && npm i -g opencode-ai@latest @openai/codex @mariozechner/pi-coding-agent \
    && npm cache clean --force \
    && uv python install 3.12 \
    && uv cache prune --ci || true

# pi cannot be pointed at an OpenAI-compatible endpoint by environment variable.
# Tested: with OPENAI_BASE_URL set it still authenticates against api.openai.com
# and returns 401, and its only base-URL variable is Azure's. A custom provider
# in models.json is the supported way, so the gateway is one -- `apiKey` names an
# environment variable rather than holding a secret, which is why this can ship
# in an image.
#
# Select it with `model_name: gateway/gemma`: harbor's pi adapter splits that
# into `--provider gateway --model gemma`.
#
# Points at the deployed gateway. Running a local one (`just ops::gateway`) means
# overriding this file, or pi will talk to the deployed gateway instead.
# printf rather than a heredoc: a Dockerfile heredoc is a BuildKit feature, the
# workers build without buildx, and the legacy builder writes a zero-byte file
# and reports success. pi then says `Unknown provider "gateway"` while every log
# line says the base image is ready.
RUN mkdir -p /root/.pi/agent \
 && printf '%s\n' \
      '{' \
      '  "providers": {' \
      '    "gateway": {' \
      '      "baseUrl": "https://bench-llm.playground.dataminded.cloud/v1",' \
      '      "api": "openai-completions",' \
      '      "apiKey": "OPENAI_API_KEY",' \
      '      "authHeader": true,' \
      '      "models": [{ "id": "gemma" }]' \
      '    }' \
      '  }' \
      '}' \
      > /root/.pi/agent/models.json \
 && python3 -c "import json;json.load(open('/root/.pi/agent/models.json'))"

# codex sandboxes every command with bubblewrap, which needs unprivileged user
# namespaces a task container does not have: every command fails with "bwrap: No
# permissions to create a new namespace", and codex concludes it cannot see the
# filesystem. The container is already the sandbox.
#
# Written to /tmp/codex-home, not ~/.codex, because harbor's adapter sets
# CODEX_HOME to that path -- a config in the home directory is never read, which
# is why this looked fixed in a hand-run container and was not fixed under
# harbor. The adapter appends its own openai_base_url line to this file, so it
# has to exist rather than be created by it.
#
# The provider block is the adapter's job; what it does not set is the sandbox.
RUN mkdir -p /tmp/codex-home \
 && printf '%s\n' \
      'sandbox_mode = "danger-full-access"' \
      'approval_policy = "never"' \
      > /tmp/codex-home/config.toml \
 && chmod 777 /tmp/codex-home \
 && grep -q danger-full-access /tmp/codex-home/config.toml

# The duckdb CLI. Three tasks ship a duckdb database and pip-install the python
# library, but `duckdb file.db ".schema"` is the first thing every harness
# reaches for -- and command-not-found there reads as a broken environment
# rather than a missing tool: codex spotted both bugs in duckdb-nulls, hit this,
# and then looped narrating instead of running anything else.
#
# duckdb-cli, not duckdb: the PyPI `duckdb` package provides no executable.
# 1.4.4 rather than the tasks' 1.4.1 -- same storage format, and the CLI is not
# published at 1.4.1.
RUN uv tool install duckdb-cli==1.4.4 \
 && uv cache prune --ci || true
RUN duckdb -c "select 42" | grep -q 42


# mini-swe-agent, installed exactly as harbor's adapter installs it, so its own
# install step finds the work already done instead of repeating it.
#
# `--with litellm[proxy]` is harbor's spelling, not ours: mini-swe-agent resolves
# the gateway through litellm, and without the extra it starts and cannot route.
RUN uv tool install mini-swe-agent --with 'litellm[proxy]' \
 && mini-swe-agent --help > /dev/null

# openhands is not installed, because it cannot currently be run at all. Harbor
# verifies its install with `python -m openhands.core.main --version`, and that
# module is gone from openhands-ai 1.x (the package is `openhands.sdk` now);
# every 0.x release the adapter can still drive -- 0.47, 0.48, 0.49 -- fails the
# same command with `No module named 'deprecated'`, a dependency those releases
# do not declare. Installing `Deprecated` alongside fixes it, which the adapter
# has no way to express. Measured 2026-08-19.

# swe-agent is deliberately absent: it runs `rm -rf /opt/sweagent-repo` and
# re-clones on every rollout, so anything baked for it is deleted before use.
#
# What it does need is /testbed. Harbor's adapter picks the repository to work on
# with, literally:
#
#   $(if [ -d /testbed ]; then echo '--env.repo.type=preexisting --env.repo.repo_name=/testbed';
#     else echo '--env.repo.path=$(pwd)'; fi)
#
# The `$(pwd)` in the else branch is inside single quotes, so it is echoed rather
# than expanded, and command substitution does not re-evaluate its own output --
# swe-agent is handed the four characters `$(pwd)` as a path and dies in
# check_valid_repo on `/app/$(pwd)`. The if branch has no such bug, so the fix is
# to take it: a symlink is enough for `-d`, and `preexisting` works in place, so
# swe-agent edits /app and the verifier reads what it wrote.
RUN ln -s /app /testbed

# swe-agent needs SWEAGENT_CONFIG=/opt/sweagent-configs/default_backticks.yaml,
# set by the worker, `just eval` and the canary. Nothing to install for it here;
# the note is here because this is where the other harness config files live.
#
# Why: swe-agent asks litellm whether the model supports function calling, gets
# False for a name litellm has never heard of, warns, and then parses the reply
# as a tool call anyway -- every rollout submitted an empty patch. Its own advice
# is `parse_function='thought_action'`, and setting only that renders an empty
# user message: the parser and the prompt templates are a matched pair, and
# default.yaml's templates are written for the function-calling path.
# default_backticks.yaml is upstream's matched pair for prose commands, staged by
# harbor's own install. Measured both ways, 2026-08-19.

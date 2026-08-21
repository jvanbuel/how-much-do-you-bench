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
    && npm i -g opencode-ai@latest @mariozechner/pi-coding-agent \
    && npm cache clean --force \
    && uv python install 3.12 \
    && uv cache prune --ci || true

# nvm puts node under $NVM_DIR/versions/node/<version>/bin, which is on the
# PATH only for a shell that has sourced nvm.sh. A login shell re-sources
# /etc/profile and drops it, so `npx` is not found -- and an MCP server
# configured as `command: npx` fails to spawn, which looks exactly like an MCP
# server that started and turned out not to help.
#
# Symlinks rather than ENV PATH, because /usr/local/bin survives a login shell
# and an ENV PATH does not. Resolved from nvm rather than written out, so a
# version bump above does not leave these pointing at nothing.
#
# Verified in the same layer: a broken link should fail the build here rather
# than a rollout an hour later.
RUN . "$NVM_DIR/nvm.sh" \
    && NODE_BIN="$(dirname "$(nvm which current)")" \
    && for b in node npm npx; do ln -sf "$NODE_BIN/$b" "/usr/local/bin/$b"; done \
    && env -i /bin/sh -lc 'node --version && npx --version' \
    && env -i /bin/sh -lc 'command -v opencode >/dev/null || true'

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

# The duckdb CLI. Three tasks ship a duckdb database and pip-install the python
# library, but `duckdb file.db ".schema"` is the first thing every harness
# reaches for -- and command-not-found there reads as a broken environment
# rather than a missing tool: codex spotted both bugs in duckdb-nulls, hit this,
# and then looped narrating instead of running anything else.
#
# duckdb-cli, not duckdb: the PyPI `duckdb` package provides no executable.
# 1.4.4 rather than the tasks' 1.4.1 -- same storage format, and the CLI is not
# published at 1.4.1.
#
# The parentheses matter: `install && prune || true` lets a failed *install*
# produce a successful layer, and the failure then surfaces one line down as
# `duckdb: not found` in a layer that looks unrelated. Only the prune is
# allowed to fail.
RUN uv tool install duckdb-cli==1.4.4 && (uv cache prune --ci || true)
RUN duckdb -c "select 42" | grep -q 42

# Not installed, and not by omission: codex, mini-swe-agent, swe-agent and
# openhands are unsupported against this model. Each failure and how far it was
# chased is in docs/harnesses.md -- read that before adding any of them back,
# because three of the four fail on the same thing and it is not fixable here.

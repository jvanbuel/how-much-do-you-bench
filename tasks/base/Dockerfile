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
    && rm -rf /var/lib/apt/lists/*

# uv, for the task's own dependencies and for the pip-installed harnesses.
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /usr/local/bin/

# The harnesses. Trimmed to those that can talk to an OpenAI-compatible
# endpoint, which is what the gateway serves: claude-code needs the Anthropic
# API and mostly fails against this model, and gemini-cli and qwen-code target
# their own providers. Harbor installs any other harness on demand -- this only
# decides which ones are already warm.
#
# Versions track harbor.agents.installed.node_install, so `nvm install 22` and
# `npm i -g` become checks rather than downloads.
RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash \
    && . "$NVM_DIR/nvm.sh" \
    && nvm install 22 \
    && npm i -g opencode-ai@latest @openai/codex @mariozechner/pi-coding-agent \
    && npm cache clean --force \
    && uv python install 3.12 \
    && uv tool install aider-chat \
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

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
# namespaces that a task container does not have. Without this it fails every
# command with "bwrap: No permissions to create a new namespace", concludes it
# cannot see the filesystem, and asks the user for help -- which is what a
# whole 21-task submission of zeros turned out to be. The container is already
# the sandbox; a second one inside it buys nothing.
#
# The provider block is not optional either: codex ignores OPENAI_BASE_URL
# unless a provider declares it, and `wire_api = "chat"` was removed, so the
# responses API is the only wire left.
RUN mkdir -p /root/.codex \
 && printf '%s\n' \
      'model_provider = "gateway"' \
      'sandbox_mode = "danger-full-access"' \
      'approval_policy = "never"' \
      '' \
      '[model_providers.gateway]' \
      'name = "gateway"' \
      'base_url = "https://bench-llm.playground.dataminded.cloud/v1"' \
      'env_key = "OPENAI_API_KEY"' \
      'wire_api = "responses"' \
      > /root/.codex/config.toml \
 && grep -q 'danger-full-access' /root/.codex/config.toml

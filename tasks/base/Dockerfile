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

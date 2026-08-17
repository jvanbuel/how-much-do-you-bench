FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pinned so the environment is reproducible and the verifier needs no network.
RUN pip install --no-cache-dir \
      dbt-core==1.9.4 \
      dbt-duckdb==1.9.3 \
      duckdb==1.4.1 \
      polars==1.35.1 \
      pytest==8.4.1 \
      pytest-json-ctrf==0.3.5

# uv is how participant agents install their own dependencies.
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pipeline/ /app/pipeline/
COPY run_pipeline.sh /app/run_pipeline.sh
COPY generate_data.py /app/generate_data.py
RUN chmod +x /app/run_pipeline.sh

# The feed is generated rather than committed: deterministic from a fixed
# seed, and large enough that reading it tells you nothing.
RUN python /app/generate_data.py

# Pre-install the harnesses. Harbor installs them on every rollout regardless;
# finding them already present turns that into a version check rather than a
# download. Versions track harbor.agents.installed.node_install -- see
# tasks/AGENTS.md.
ENV NVM_DIR=/root/.nvm
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash \
    && . "$NVM_DIR/nvm.sh" \
    && nvm install 22 \
    && npm i -g opencode-ai@latest @anthropic-ai/claude-code @openai/codex \
         @google/gemini-cli @qwen-code/qwen-code \
    && npm cache clean --force

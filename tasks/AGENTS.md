# Notes for authoring task images

## The container has to stay alive

If the base image's `ENTRYPOINT`/`CMD` exits -- `apache/airflow` runs `airflow`,
which prints help and returns -- the task container stops the moment it starts,
and every exec Harbor makes into it dies with **exit 137**. That reads like an
out-of-memory kill and is not one: raising `memory_mb` changes nothing, `dmesg`
is clean, and the same command run by hand in the same image succeeds. End such
a Dockerfile with:

```dockerfile
ENTRYPOINT []
CMD ["sleep", "infinity"]
```

## Pre-installing the harnesses in a task image

Harbor installs the chosen harness into the task container on every rollout, and
it does so unconditionally -- there is no "already present, skip" path. A cold
install means an nvm download, a Node toolchain and an npm global install before
the agent has read a single line of the task, on every trial.

Baking the same things into the image does not stop the install step running. It
makes it a no-op check instead of a download: `nvm install 22` finds 22 already
there, `npm i -g opencode-ai@latest` finds the latest already there. Minutes
become seconds, and a rollout stops depending on npm being reachable.

Copy this block into a task's Dockerfile, below whatever the task itself needs.
It has to run as the same user Harbor runs the agent as -- `root` in these
images -- because nvm installs into `$HOME`.

```dockerfile
# Pre-install the harnesses. Harbor installs them anyway; finding them present
# turns that into a version check rather than a download. Versions match
# harbor.agents.installed.node_install so nvm treats them as satisfied.
ENV NVM_DIR=/root/.nvm
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash \
    && . "$NVM_DIR/nvm.sh" \
    && nvm install 22 \
    && npm i -g \
         opencode-ai@latest \
         @anthropic-ai/claude-code \
         @openai/codex \
         @google/gemini-cli \
         @qwen-code/qwen-code \
         @mariozechner/pi-coding-agent \
    && npm cache clean --force
```

Keep the nvm version and Node major in step with Harbor. If Harbor bumps
`NVM_VERSION` or `DEFAULT_NODE_MAJOR`, a stale image silently goes back to
downloading a second Node, which is slower than not pre-installing at all.

The pip-installed harnesses go through uv, which keeps them out of the task's own
Python environment:

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy
ENV PATH="/root/.local/bin:$PATH"
RUN uv python install 3.12 \
    && uv tool install aider-chat \
    && uv cache prune --ci || true
```

swe-agent is deliberately not pre-installed: it runs `rm -rf /opt/sweagent-repo`
and re-clones every time, so a baked copy is deleted before it is used. What does
help is already above -- it checks for `uv` before fetching it, and
`uv python install 3.12` is the download that actually costs time.

`uv tool install` puts binaries in `/root/.local/bin`, hence the PATH line. Note
that a **login** shell (`bash -lc`) re-sources `/etc/profile` and discards the
image's PATH, so `command -v aider` can fail there while working fine for the
harness -- do not chase that as a missing install.

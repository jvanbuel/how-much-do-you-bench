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
    && npm cache clean --force
```

Keep the nvm version and Node major in step with Harbor. If Harbor bumps
`NVM_VERSION` or `DEFAULT_NODE_MAJOR`, a stale image silently goes back to
downloading a second Node, which is slower than not pre-installing at all.

Harnesses that install with pip rather than npm (aider, swe-agent) are not
included: they resolve into the task's own Python environment, and pinning them
here risks a dependency conflict with what the task itself needs.

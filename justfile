set dotenv-load := true
gateway_url := env_var_or_default("GATEWAY_URL", "https://bench-llm.playground.dataminded.cloud/v1")
api_url := env_var_or_default("API_URL", "https://bench.playground.dataminded.cloud")

# The deployed gateway, so a fresh clone can run a task with nothing but a team
# key. Point GATEWAY_URL at http://host.docker.internal:4000/v1 to use a local
# `just gateway` instead.

# Run your working tree against a sample task. No commit needed.
#
# The harness comes from agent.yaml in the repo root, so switching to a
# different harness or attaching skills and MCP servers is an edit to that
# file rather than a change to this command.
#
# Run one task against your agent, no commit needed.
eval task="incremental-dupes" agent_dir="agent":
    #!/usr/bin/env bash
    set -euo pipefail
    # Harbor will not resume a job directory whose config changed, and this is
    # the iterate-and-look loop, so it has to be re-runnable.
    rm -rf jobs/local
    KEY="${GATEWAY_API_KEY:-${LITELLM_MASTER_KEY:-}}"
    [ -n "$KEY" ] || { echo "set GATEWAY_API_KEY to your team key (see the kickoff message)"; exit 1; }
    # An Anthropic-speaking harness reads these; ours reads GATEWAY_*. Passing
    # both lets agent.yaml decide without this command knowing which.
    #
    # Claude Code appends /v1/messages itself, so it wants the host root. Handed
    # the OpenAI-style base it posts to /v1/v1/messages and reports the model as
    # missing, which reads like a bad model name rather than a bad URL.
    GW="${GATEWAY_URL:-{{gateway_url}}}"
    export ANTHROPIC_BASE_URL="${GW%/v1}"
    # Gemma on Bedrock refuses function tools whenever a reasoning request comes
    # with them, and an Anthropic-format `thinking` block becomes exactly that in
    # translation. Every agent here calls tools, so adaptive thinking is off.
    export CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1
    export OPENAI_BASE_URL="$GW"

    MOUNTS='[{"type":"bind","source":"{{justfile_directory()}}/{{agent_dir}}","target":"/submission-src","read_only":true}'
    CA=()
    # Behind a TLS-intercepting proxy the agent's HTTPS call fails with a bare
    # connection error that never mentions TLS. See CORP_CA_FILE in the README.
    if [ -f "${CORP_CA_FILE:-}" ]; then
      MOUNTS+=',{"type":"bind","source":"'"$CORP_CA_FILE"'","target":"/etc/ssl/ca.pem","read_only":true}'
      CA=(--ae SSL_CERT_FILE=/etc/ssl/ca.pem --ae REQUESTS_CA_BUNDLE=/etc/ssl/ca.pem)
    fi

    # harbor is a uv tool, so the repo is not on its sys.path.
    PYTHONPATH={{justfile_directory()}}/ops harbor run \
      --config agent.yaml -p tasks/{{task}} -n 1 -o jobs --job-name local -y \
      --mounts "$MOUNTS]" \
      --ae SUBMISSION_LOCAL_DIR=/submission-src \
      --ae GATEWAY_URL="$GW" \
      --ae GATEWAY_API_KEY="$KEY" --ae ANTHROPIC_API_KEY="$KEY" \
      --ae OPENAI_API_KEY="$KEY" \
      "${CA[@]}"

# Refuses uncommitted or unpushed work and sends the full commit hash, because
# each of those fails twenty minutes later rather than immediately.
#
# Submit your agent for grading.
submit team:
    #!/usr/bin/env bash
    set -euo pipefail
    git diff --quiet && git diff --cached --quiet \
      || { echo "you have uncommitted changes; commit them first"; exit 1; }
    git fetch -q origin
    BRANCH=$(git rev-parse --abbrev-ref HEAD)
    git merge-base --is-ancestor HEAD "origin/$BRANCH" 2>/dev/null \
      || { echo "HEAD is not on origin/$BRANCH; run: git push"; exit 1; }
    SHA=$(git rev-parse HEAD)
    URL=$(git remote get-url origin | sed -e 's#^git@github.com:#https://github.com/#' -e 's#\.git$##')
    echo "submitting $SHA to $URL"
    curl -fsS -X POST {{api_url}}/submit -H 'content-type: application/json' \
      -d "{\"team\":\"{{team}}\",\"repo_url\":\"$URL\",\"commit\":\"$SHA\"}"
    echo

show-config agent_dir="agent":
    PYTHONPATH={{justfile_directory()}}/ops harbor run --config agent.yaml -p tasks/incremental-dupes --print-config

# Confirm a task is solvable and that its verifier actually fails when unsolved.
calibrate task="incremental-dupes":
    rm -rf jobs/oracle jobs/nop
    harbor run -p tasks/{{task}} -a oracle -n 1 -o jobs --job-name oracle -y
    harbor run -p tasks/{{task}} -a nop -n 1 -o jobs --job-name nop -y
    @echo "oracle must score 1.0 and nop must score 0.0"

# Trajectories, results and timings for every run in jobs/. No infrastructure:
# it reads the directory Harbor already wrote.
#
# See what your agent actually did. Start here after every eval.
view:
    harbor view jobs

# Everything for running the event: deploy, keys, fleet, reset.
mod ops "ops/justfile"

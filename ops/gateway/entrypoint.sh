#!/bin/sh
# Start LiteLLM one port over, and the tool-id proxy on the port the task
# definition publishes.
#
# The port has to be rewritten here rather than in terraform: ECS passes
# `--config /app/config.yaml --port 4000` as the container command, and
# changing that is a terraform apply. An entrypoint is not overridden by a
# command, so this can move LiteLLM without touching the deployment.
#
# Why the proxy exists at all is in tool_id_proxy.py: LiteLLM runs no post-call
# hook on /v1/messages, so the one fix Claude Code needs cannot go in hooks.py
# with the others.
set -eu

UPSTREAM_PORT="${LITELLM_INTERNAL_PORT:-4001}"
PUBLISHED_PORT=4000

# Take the published port from the command if it names one, so this keeps
# working if the task definition ever changes it.
prev=""
for arg in "$@"; do
  [ "$prev" = "--port" ] && PUBLISHED_PORT="$arg"
  prev="$arg"
done

# Same arguments, with the port pointed at the internal one.
set -- $(printf '%s\n' "$@" | sed "s|^${PUBLISHED_PORT}\$|${UPSTREAM_PORT}|")

echo "gateway: litellm on ${UPSTREAM_PORT}, proxy on ${PUBLISHED_PORT}" >&2

# LiteLLM is the process that matters: if it dies the container should die with
# it rather than serve 502s behind a healthy-looking proxy.
litellm "$@" &
LITELLM_PID=$!

TOOL_ID_PROXY_UPSTREAM="http://127.0.0.1:${UPSTREAM_PORT}" \
  python /app/tool_id_proxy.py "${PUBLISHED_PORT}" &
PROXY_PID=$!

# Exit as soon as either half stops, so ECS replaces the task.
while :; do
  kill -0 "$LITELLM_PID" 2>/dev/null || { echo "gateway: litellm exited" >&2; exit 1; }
  kill -0 "$PROXY_PID" 2>/dev/null || { echo "gateway: proxy exited" >&2; exit 1; }
  sleep 5
done

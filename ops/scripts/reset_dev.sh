#!/usr/bin/env bash
# Wipe every result of a practice run: the table, the queues, the job files.
#
# Used between a rehearsal and the real event, so the board starts empty and no
# debug submission is mistaken for a team's. Deletes data and cannot be undone,
# so it asks first unless given --yes.
set -euo pipefail

PROFILE="${AWS_PROFILE:-playground}"
REGION="${AWS_REGION:-eu-central-1}"
CLUSTER="${CLUSTER:-de-benchmark}"
TABLE="${TABLE:-de-benchmark-results}"
QUEUE="${QUEUE:-de-benchmark-rollouts}"

aws() { command aws --profile "$PROFILE" --region "$REGION" "$@"; }

account=$(aws sts get-caller-identity --query Account --output text)
queue_url="https://sqs.${REGION}.amazonaws.com/${account}/${QUEUE}"
dlq_url="${queue_url}-dlq"

rows=$(aws dynamodb scan --table-name "$TABLE" --select COUNT --query Count --output text)
depth=$(aws sqs get-queue-attributes --queue-url "$queue_url" \
  --attribute-names ApproximateNumberOfMessages \
  --query 'Attributes.ApproximateNumberOfMessages' --output text)
dlq_depth=$(aws sqs get-queue-attributes --queue-url "$dlq_url" \
  --attribute-names ApproximateNumberOfMessages \
  --query 'Attributes.ApproximateNumberOfMessages' --output text)

echo "About to delete:"
echo "  $rows rows from $TABLE"
echo "  $depth queued + $dlq_depth dead-lettered messages"
echo "  every job directory on EFS"

if [ "${1:-}" != "--yes" ]; then
  # A task runner gives the recipe no terminal, so the prompt would read EOF
  # and look like a refusal. Say what to run instead.
  [ -t 0 ] || { echo "not a terminal; re-run as: just reset-dev --yes"; exit 1; }
  read -r -p "Type 'reset' to continue: " reply
  [ "$reply" = "reset" ] || { echo "aborted"; exit 1; }
fi

# One process for the whole table. It was one `aws delete-item` per row, and the
# cost was never the API: starting the CLI costs about a second in interpreter
# startup and credential resolution, so a few hundred rows spent minutes
# spawning processes.
echo "clearing $TABLE ..."
AWS_PROFILE="$PROFILE" uv run --quiet --with boto3 \
  python "$(dirname "$0")/reset_rows.py" "$REGION" "$TABLE"

# PurgeQueue is rate limited to once a minute per queue; the second call fails
# rather than waits, so the failure is tolerated and reported.
echo "purging queues ..."
aws sqs purge-queue --queue-url "$queue_url" || echo "  main queue purge refused (once per minute)"
aws sqs purge-queue --queue-url "$dlq_url" || echo "  dlq purge refused (once per minute)"

# The files live on the instance's EFS mount, not locally, so this runs there.
echo "clearing job files ..."
iid=$(aws ec2 describe-instances \
  --filters "Name=tag:aws:autoscaling:groupName,Values=${CLUSTER}-worker" \
            "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)

if [ "$iid" = "None" ] || [ -z "$iid" ]; then
  echo "  no running worker instance; EFS left untouched"
else
  cmd=$(aws ssm send-command --instance-ids "$iid" \
    --document-name AWS-RunShellScript \
    --parameters 'commands=["rm -rf /app/jobs/runs","mkdir -p /app/jobs/runs","echo cleared"]' \
    --query 'Command.CommandId' --output text)
  # Waited a flat 8s and printed whatever was there, which reported success
  # while a large run directory was still being removed. EFS deletes a file at a
  # time over NFS, so this is the slow step and worth waiting for.
  for _ in $(seq 1 40); do
    state=$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$iid" \
      --query Status --output text 2>/dev/null || echo Pending)
    case "$state" in InProgress|Pending|Delayed) sleep 5 ;; *) break ;; esac
  done
  aws ssm get-command-invocation --command-id "$cmd" --instance-id "$iid" \
    --query 'StandardOutputContent' --output text | sed 's/^/  /'
fi

echo "done. the board is empty."

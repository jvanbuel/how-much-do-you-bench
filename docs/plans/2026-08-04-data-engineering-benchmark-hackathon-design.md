# Data Engineering Agent Benchmark Hackathon

Design, 2026-08-04.

## Goal

Teams optimize an agent against a benchmark of data engineering tasks. Everyone
gets the same model (Gemma 4 31B). Teams differ only in their harness, skills,
MCP servers, and context engineering. Baseline agent scores about 30%.

Event: 5 to 10 teams, 3 to 4 hours, max 5 submissions per team, 10 to 20 tasks.

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Submission unit | Repo + commit hash, all tasks | Custom harnesses stay possible, no image builds in the loop |
| Sandbox | Harbor (Terminal-Bench), task = Docker image + pytest verifier | Already used in-house |
| Task stack | DuckDB, dbt, Polars, fixtures baked in, no network | Runs on a laptop, no cloud credentials in participant code |
| Task visibility | 5 public samples, 10 to 15 held-out in a private repo | A branch is not a secret |
| Model access | LiteLLM in front of Bedrock mantle | Virtual keys, per-key token accounting and rate limits |
| Scoring | Binary per task, Pareto scatter of passed vs tokens | Ratios with tokens in the denominator reward giving up |
| Queue | One SQS message per (submission, task) | Per-task retries, executor stays swappable |
| Results | One DynamoDB table, scanned by the dashboard | 1000 items fits one scan page |
| Trajectories | Harbor's own viewer over the run directory | No extra service, and it answers the question teams actually ask |
| Infra | Terraform, S3 state with `use_lockfile` | |

## Architecture

Three repos:

- `hackathon-agent-template` (public). What teams fork. `pyproject.toml` with
  `[project.scripts] agent = "agent.main:cli"`, a baseline agent of about 100
  lines, 5 sample tasks, `just eval <task>` and `just replay <run-id>`.
- `hackathon-tasks` (private). All tasks in Harbor format. Workers clone with a
  deploy key.
- `hackathon-platform` (private). Submit API, worker, dashboard, Terraform.

Flow:

1. Team pushes to their fork, calls `POST /submit {repo_url, commit}`.
2. API validates the commit, enforces the 5-submission cap, writes a `_meta`
   item, enqueues one SQS message per task.
3. Worker long-polls SQS. Per message: clone team repo at commit, build task
   image, run Harbor with the `submission` agent adapter, restore test files,
   run the verifier, upload the trajectory to S3, write one DynamoDB item,
   delete the message.
4. Dashboard scans DynamoDB and draws the Pareto scatter.

Visibility timeout 15 minutes, `maxReceiveCount: 2`, then DLQ. A task that kills
a worker twice scores zero instead of looping.

### The agent adapter

One Harbor agent, `submission`, parameterized by repo and commit:

- install: `git clone && git checkout $COMMIT && uv sync --frozen`
- run: `uv run agent --task-dir /task --model gemma --gateway $GATEWAY_URL`

The entrypoint contract is the only thing fixed. Everything behind it is the
team's: LangGraph, Pydantic AI, a raw loop, anything.

### Harness configuration

Teams do not choose a harness with CLI flags. `template/agent.yaml` is a Harbor
JobConfig, giving `name` or `import_path`, `model_name`, `skills`,
`mcp_servers`, `kwargs` and `env` per agent. Swapping to an off-the-shelf
harness, attaching a skill directory or wiring an MCP server is an edit to that
file.

Because `agents` is a list, one file can run several harnesses against the same
task in a single command, which makes "is my context engineering actually
beating mini-swe-agent" a measurement rather than an assumption.

This is Harbor's own config object rather than a format we invented, so it
cannot drift from what the runner does, and it is the same object the workers
use for scored runs.

### The gateway is the meter

Token counts come from the gateway, never from participant code, because the
metric would otherwise be self-reported by the people being scored.

The worker mints a gateway API key per rollout, named `submission_id::task_id`,
passes it to the agent, reads that key's usage when the rollout ends, and
retires the key. Attribution is therefore exact and server-side, and it does not
depend on the agent cooperating. Per-key rate limits stop one runaway loop from
starving the queue.

## Tasks

### Verifier rules

- Restore test files from the image before grading. The agent must not be able
  to make the verifier pass by editing it.
- Assert on output data (row counts, checksums, specific values), never on the
  shape of the code.
- Freeze nondeterminism: fixed seeds, pinned `now()`, no network.

### Families

The pattern that defeats a 31B model: the bug is not in the file you would
naturally open, and finding it requires running something and reading the
output. Its failure mode is patching the visible symptom.

- Incremental model double-counts on late-arriving rows (wrong merge key)
- Fan-out join silently inflates a revenue metric
- Timezone/DST boundary corrupts daily aggregates
- SCD Type 2 build from an out-of-order change feed
- Upstream schema evolution breaks a downstream model, needs a backfill
- Dedup with a non-obvious tie-break rule
- A query that OOMs and must be rewritten to stream
- Reconciliation: two sources disagree, produce a diff report to spec

### Calibration

Run the baseline agent 5 times per candidate task. Keep tasks between 0% and
60%. Drop anything at 100% (no signal). Treat 0% across all 5 as broken until
proven hard. Target a distribution, not a flat 30%: three or four tasks at 60 to
70% so every team scores, the bulk at 20 to 40%, one or two near zero as a
tiebreaker. The mean lands near 30% on its own.

Budget half a day per task to author and calibrate. Twenty tasks is ten days of
work, and that is the real schedule risk.

### What calibration actually taught us

The first version of `incremental-dupes` scored **5/5** for the baseline agent.
The trajectory showed why in one line: turn 3 was `cat raw_orders.csv`, and the
fixture was six rows. With the whole feed on one screen, both the restatement
and the late arrival are visible by eye, so the task tested reading rather than
diagnosis.

Regenerating the feed at 30,000+ rows, with restatements and late arrivals at
about one percent each, moved it to **1/5**. Nothing else about the task
changed: same bug, same fix, same verifier logic.

So **fixture size is the difficulty lever**, more than the cleverness of the
bug. A bug you can see is not a bug you have to diagnose. Author every task so
the symptom is only reachable by aggregating, and state the symptom the way
finance would: the numbers disagree, not "there is a duplicate".

The failure mode at 1/5 is the useful one. The agent locates the right model,
then rewrites it five or six times without converging, one run hitting the
40-turn ceiling exactly. It is losing to its own feedback loop, not to the
SQL, and that is what context engineering fixes.

## Scoring

Binary per task from the verifier exit code. Per-test results are recorded but
not scored, so teams get useful feedback without being able to farm partial
credit.

The dashboard's main view is a Pareto scatter: total tokens on X, tasks passed
on Y, non-dominated points highlighted. Under it, two ranked tables:

- Accuracy: tasks passed, tiebreak fewer tokens.
- Efficiency: fewest tokens, among teams clearing 50% of the top score. The
  floor is what kills the give-up strategy.

`score / tokens` is rejected: a team passing 1 task on 2k tokens beats a team
passing 15 on 200k by a factor of six.

## Trajectories

`harbor view jobs` serves Harbor's own viewer over the run directory: which
trials passed, rewards, durations, and everything the agent printed. No extra
service, no auth to configure, and it is already installed.

The worker keeps each rollout's job directory rather than discarding it with a
temp dir, so a graded failure can be opened the same way.

### Why not a trace store

MLflow was in this design and came out. It only did trace viewing, and the two
things that would have justified it both failed on inspection: per-team
filtering of gateway traces needs LiteLLM key-level tags, which are an
Enterprise feature, and a shared open-source MLflow has no access control, so
every team could read every other team's prompts. That left a container, a
callback and an unsolved auth problem duplicating what Harbor gives free.

Teams wanting richer traces than the agent's own stdout can instrument their
harness however they like. It is their agent, and choosing how to observe it is
part of the exercise.


## Capacity

10 teams x 5 submissions x 20 tasks = 1000 rollouts in 4 hours. At 4 minutes
each that is about 17 concurrent rollouts sustained, with a spike when everyone
submits in the last 20 minutes.

The ceiling is Gemma throughput, not CPU. Ten teams iterating locally is a
continuous background load competing with graded runs for the same Bedrock
quota. Per-team RPM limits are what stop one runaway retry loop from starving
the leaderboard. Size quota for local iteration plus peak submissions, and load
test the week before.

## Executor

Deliberately the last decision. The SQS message is `{submission_id, task_id,
repo, commit}` regardless of what consumes it, so this is reversible.

- Laptop poll loop. For the slice. About 30 lines.
- Fixed EC2 fleet, sized to peak, up an hour early, torn down after. Two
  `c7i.4xlarge` for 5 hours is about $15. Autoscaling saves single-digit dollars
  and costs a scale-out policy that must behave during the one spike that
  matters.
- CodeBuild. Privileged mode supported, so Docker-in-Docker works and Harbor
  runs unmodified. Lambda consumes SQS and calls `StartBuild`.

Rejected: Lambda as executor (no Docker daemon, no privileged mode, 15 minute
cap). Fargate as executor (no privileged mode, so no Docker-in-Docker; only
works if the Fargate task is itself the sandbox, which means dropping Harbor and
one task definition per benchmark task). AgentCore (built for serving agent
sessions, adapter glue against an unfamiliar service weeks before the event).

Pick after measuring one real rollout. At 4 minutes CodeBuild is fine. At 12,
a warm fleet is the correct choice rather than the boring one.

## Vertical slice

One task (`incremental-dupes`), end to end, plus the dashboard.

- Template repo with the baseline agent and `just eval`.
- LiteLLM in `docker compose`, pointed at Bedrock.
- One FastAPI app serving `POST /submit`, `GET /results`, and the static
  dashboard.
- Worker poll loop on the laptop.
- Terraform `core.tf`: SQS queue, DLQ, DynamoDB table, worker IAM policy. No
  VPC, no ECR, no compute.
- Real AWS, not LocalStack. Cents at this volume, and no emulator drift to debug
  on the day.

DynamoDB item, `PK=submission_id`, `SK=task_id`:

```
{submission_id, task_id, team, commit, status, passed,
 tests_passed, tests_total, tokens_in, tokens_out,
 duration_s, trajectory_key, error}
```

Plus one `SK="_meta"` item per submission with team, commit, created_at,
task_count. No GSI, no aggregation table.

Dashboard: one HTML file, no build step, vanilla JS, inline SVG, 10 second
poll.

Done when: push a commit to a fork, `POST /submit`, watch the worker pick it up,
see the point appear on the scatter with a working trajectory link.

The slice answers the three real risks early. Does Bedrock serve Gemma the way
we need. Does Harbor's agent override work with a uv-based custom entrypoint. Is
one task hard enough.

## Model access (resolved 2026-08-04)

Gemma 4 31B is `google.gemma-4-31b`, served **only** on the `bedrock-mantle`
endpoint. Not on `bedrock-runtime`: Converse, InvokeModel and Messages are all
unsupported, so LiteLLM's `bedrock/...` provider cannot reach it.

Mantle is OpenAI-compatible with bearer auth against a Bedrock long-term API
key, so the gateway configures it as a plain OpenAI base URL:
`https://bedrock-mantle.{region}.api.aws/openai/v1`. No SigV4.

This makes the Scaleway migration symmetric rather than a rewrite: both
providers are an OpenAI-compatible base URL plus a key, so the swap is one
config block. It also narrows what the gateway is for. It is not provider
abstraction, since the provider API is already the same on both sides. It is
per-team keys, rate limits and the token meter.

Regions: us-east-1, us-east-2, us-west-2, eu-central-1. Not eu-west-1, so the
EU deployment is Frankfurt and the queue and table moved with it.

Constraints that shape the harness and the tasks:

- **No parallel tool calls.** One per turn, for every team.
- **Reasoning content only on the Responses API.** Chat Completions bills the
  reasoning tokens but never returns them, which matters for a hackathon whose
  premise is reading trajectories.
- 256K context, 3.5 MB request payload cap.
- **Throughput ramps.** On mantle, available throughput scales with use and
  default limits are not exposed through Service Quotas at all. AWS states that
  not all in-quota requests succeed under load. The capacity risk is therefore a
  warm-up curve rather than a fixed ceiling to request an increase against, and
  the event's worst spike lands in its final 20 minutes. Warm the account before
  the day; consider the Priority tier.

## Open

- Whether held-out tasks are fresh instances of the public sample families
  (better generalization signal, roughly double the authoring work) or distinct
  tasks. Decide per family.
- Whether to expose the Responses API through the gateway so teams can see
  reasoning content.

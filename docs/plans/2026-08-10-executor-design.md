# How the tasks get executed

Decision doc, 2026-08-10. Question: what runs the Harbor rollouts during the
event, and can it be Lambda?

## Short answer

**Yes, Lambda works, but only with a remote Harbor environment.** Lambda cannot
run the Docker environment, and no amount of configuration changes that. With
`--env modal` Harbor becomes a pure orchestrator and Lambda is a fine host.

For the event itself I would still run a **fixed EC2 fleet**, and treat
Lambda + Modal as the scale-out option if you want it. Reasoning below.

## The measurement that decides the sizing

The first real end-to-end submission, 2026-08-10:

| | |
|---|---|
| Wall clock | **141.8 s** |
| Model requests | 20 |
| Tokens | 32,798 in / 1,366 out |
| Cost | $0.003976 |
| Result | reward 0.0 (expected four times in five) |

At ~142 s a rollout, 1000 rollouts across a 4-hour event needs about **10
concurrent workers**, not the 17 estimated from a guessed 4 minutes. The whole
event's model spend is roughly **$4**.

## Why Lambda cannot run the Docker environment

Harbor's default environment builds a task image and runs it as a container.
That needs a Docker daemon and privileged mode. Lambda has neither, and the
15-minute ceiling is a second wall. This is not a limitation to engineer
around; it is the execution model.

The same applies to **Fargate**, which has no privileged mode either, so
"Lambda dispatches to Fargate" does not rescue it. Only ECS on **EC2** can run
the Docker environment, and at that point you are managing instances anyway.

## Why Lambda can run a remote environment

Harbor ships remote environments: `modal`, `daytona`, `e2b`, `beam`,
`runloop`, `gke`, `ec2` and more. With those, the sandbox lives at the
provider and Harbor only makes API calls.

Verified rather than assumed: with `DOCKER_HOST` pointed at a nonexistent
socket, `harbor run --env modal` fails only with

    Modal requires authentication. Run 'modal token new' ...

It never touches the local daemon. Modal builds the task image server-side via
`Image.from_dockerfile()` / `from_registry()` / `from_aws_ecr()`, so nothing is
built locally either.

That makes the shape:

    SQS -> Lambda (container image, Harbor installed) -> Modal sandbox -> DynamoDB

Lambda limits are comfortable against the measurement: 900 s ceiling against a
142 s rollout, and a 10 GB image against Harbor's dependency tree.

## The catch, if you go that way

- **A second vendor in the hot path.** Two accounts to keep healthy during a
  four-hour event instead of one.
- **`[agent] timeout_sec = 900` in `task.toml` equals Lambda's hard ceiling.**
  A rollout that reaches the agent timeout leaves no time for the verifier, and
  Lambda kills the invocation mid-flight. Drop the agent timeout to ~600 s
  before running on Lambda so the verifier always gets its turn.
- **First build per task is slow on Modal**, then cached. Run each task once
  before the event or the opening minutes will look broken.
- **Modal's own concurrency and spend limits** become a second capacity
  question alongside Bedrock's.

## Recommendation

**For the event: a fixed EC2 fleet.** Two `c7i.4xlarge` for five hours is about
$15, gives roughly 10 concurrent rollouts with headroom, uses the Docker
environment already proven end to end, and adds no vendor. Start it an hour
early, tear it down after. The worker is already a poll loop; on the box it is
a systemd unit.

**If serverless is a requirement: Lambda + Modal.** It is genuinely viable,
scales without capacity planning, and costs less at idle. Budget half a day to
set up the Modal account, bake the Lambda image, pre-warm task builds, and do
a dry run.

**Rejected:** Fargate (no privileged mode, so no Docker environment).
CodeBuild works and needs no fleet, but per-build startup overhead against a
142 s rollout is poor value, and it is a stranger execution model than either
option above.

The SQS message is `{submission_id, task_id, repo, commit}` regardless of what
consumes it, so this stays reversible. Nothing downstream cares.

## What is still unverified

- No rollout has run on Modal, only the local Docker environment.
- The concurrency ceiling has not been load tested. The binding constraint is
  expected to be Bedrock mantle throughput, which ramps with use and does not
  expose its limits through Service Quotas, rather than compute.

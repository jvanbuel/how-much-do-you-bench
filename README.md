# Data Engineering Agent Benchmark

A hackathon where every team gets the same model and competes on everything
else: harness, skills, MCP servers, context engineering.

Design: [`docs/plans/2026-08-04-data-engineering-benchmark-hackathon-design.md`](docs/plans/2026-08-04-data-engineering-benchmark-hackathon-design.md)

## Start here

You need [Docker](https://docs.docker.com/get-started/), [uv](https://docs.astral.sh/uv/),
[just](https://github.com/casey/just) and [harbor](https://pypi.org/project/harbor/),
plus the team key from the kickoff message.

```
export GATEWAY_API_KEY=sk-...   # your team key: reaches the model, and identifies you when you submit
just eval                       # run a task against your agent
just view                       # what your agent actually did
just submit your-team           # when you want it scored
```

Fork this repo. Your agent is `agent/`, and `tasks/` holds five sample tasks to
develop it against.

`just eval` mounts your working tree, so there is no commit-push-wait loop: edit,
run, look, repeat. It talks to the shared gateway, so there is nothing to run
locally beyond the task container itself.

Then read the trajectory. The thing that decides your score is what ends up in
the context window, and you cannot fix what you have not looked at.

## Layout

| Path | What it is |
|---|---|
| `tasks/` | Five sample tasks. One directory per task: Dockerfile, fixtures, pytest verifier |
| `agent/` | Your agent. Baseline to beat, uv project, fixed entrypoint |
| `docs/` | Design notes |
| `harness/` | The adapter that runs `agent/` under grading. You do not edit it |

## What you are scored on

The five tasks in `tasks/` are samples, and none of them counts. Your score
comes from sixteen tasks you never see, in a private repo, baked into the
grading worker.

That split is deliberate and it is what the benchmark measures. The graders and
the reference solutions have to live somewhere, and while they lived here a
team could read the expected output and write it into its agent instead of
solving anything -- so the ranking would have gone to whoever noticed, not to
whoever built the better harness. Public dev split, private test split, the way
SWE-bench and ARC-AGI do it.

So tune for the general case. An agent that solves the samples by pattern
matching on them scores nothing. The scored sixteen are the same kind of work
-- data engineering, one container, a verifier that either passes or does not
-- and they cover the same ground: SQL and dataframes, dbt and Airflow,
Terraform, shell and git, reading someone else's run output. They are harder
than the samples on purpose.

Pick a sample you lose, find where your agent goes off the rails, fix the
context, run it again.

## Submitting

```
just submit your-team
```

It refuses uncommitted or unpushed work and sends the full commit hash, because
each of those fails twenty minutes later rather than immediately. For the same
reason the server checks the `agent.yaml` in your commit before accepting: a
config grading would refuse is a rejection now, with the reason, and costs you
nothing -- not a note on the board twenty minutes later. Ten submissions of
the full suite; local runs are unlimited and unscored. `just cancel <id>`
stops one you no longer want, but does not give it back.

## Operating the benchmark

Deployment, architecture and grading internals live in the instructor
repository, along with the scored tasks. Nothing there is needed to compete.

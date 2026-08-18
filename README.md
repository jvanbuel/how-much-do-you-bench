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

Fork this repo. Your agent is `agent/` and the benchmark is `tasks/`, in the
same tree: one clone, no second repository to keep in step.

`just eval` mounts your working tree, so there is no commit-push-wait loop: edit,
run, look, repeat. It talks to the shared gateway, so there is nothing to run
locally beyond the task container itself.

Then read the trajectory. The thing that decides your score is what ends up in
the context window, and you cannot fix what you have not looked at.

## Layout

| Path | What it is |
|---|---|
| `tasks/` | The benchmark. One directory per task: Dockerfile, fixtures, pytest verifier |
| `agent/` | Your agent. Baseline to beat, uv project, fixed entrypoint |
| `docs/` | Design notes |
| `ops/` | Everything that runs the event ([`ops/README.md`](ops/README.md)). Not needed to compete |

Every task is here and you can run any of them, which is not an invitation to
run them all: the full suite takes hours on a laptop and tells you nothing you
could not learn from one failure. Pick a task you lose, find where your agent
goes off the rails, fix the context, run it again.

## Submitting

```
just submit your-team
```

It refuses uncommitted or unpushed work and sends the full commit hash, because
each of those fails twenty minutes later rather than immediately. For the same
reason the server checks the `agent.yaml` in your commit before accepting: a
config grading would refuse is a rejection now, with the reason, and costs you
nothing -- not a note on the board twenty minutes later. Five submissions of
the full suite; local runs are unlimited and unscored.

## Operating the benchmark

Deployment, architecture, grading internals and the pre-event checklist live in
[`ops/README.md`](ops/README.md). Nothing there is needed to compete.

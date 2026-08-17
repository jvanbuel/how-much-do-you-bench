# Your agent

This directory is yours. Change whatever you want, except one thing.

## Submitting

```
just submit your-team
```

That is the only supported way in. It refuses to submit uncommitted or
unpushed work, and sends the full commit hash -- an abbreviated one cannot be
fetched from GitHub, so a hand-written request fails on checkout rather than
being graded. `make status` prints the board.

## Choosing a harness

`agent.yaml` decides what actually runs. Point it at your own code, or at an
off-the-shelf harness, and attach skills and MCP servers there:

```yaml
agents:
  - name: mini-swe-agent
    model_name: gemma
    skills:
      - ./skills/data-engineering
    mcp_servers:
      - name: duckdb
        transport: stdio
        command: uvx
        args: ["mcp-server-duckdb", "--db", "/app/warehouse.duckdb"]
    kwargs:
      max_turns: 40
```

`agents` is a list, so listing two runs both against the same task in one
command. That is the cheapest way to check whether your context engineering
actually beats an off-the-shelf harness, rather than assuming it does.

`just show-config` prints what the file resolves to without running anything.

### What skills and MCP mean for each harness

**Off-the-shelf harnesses** (claude-code, goose, …) consume both natively.
Declare them and they are installed and registered for you.

**Your own harness** gets them as environment variables, because we are not
going to force a framework on you:

| | |
|---|---|
| `SKILLS_DIR` | Directory holding the skill directories Harbor resolved. Git sources are fetched and cached before the run. |
| `MCP_CONFIG` | Path to a Claude-style `.mcp.json` written from your `mcp_servers:` block. |

Reading them is your job. The baseline agent ignores both, which is one of the
easier places to get ahead of it.

## The contract

If you write your own harness, it runs as:

```
uv run agent --instruction "<the task instruction>"
```

So `pyproject.toml` must keep:

```toml
[project.scripts]
agent = "agent.main:main"
```

Everything else is yours: a different SDK, a different loop, skills, MCP
servers, sub-agents, whatever you like. Add dependencies to `pyproject.toml`
and commit `uv.lock`; the harness installs with `uv sync --frozen`.

## The model

Gemma 4 31B. Reach it through the gateway, never directly:

- `GATEWAY_URL` and `GATEWAY_API_KEY` are in the environment.
- It speaks the OpenAI Chat Completions and Responses APIs.
- The model name is `gemma`.

Your key is rate limited, it is the only route to a model from inside a scored
run, and it is what counts your tokens.

### Things that will cost you an hour if you find them the hard way

- **One tool call per turn.** Parallel tool calls are not supported. Pass
  `parallel_tool_calls=False` and design your loop around a single call per
  turn.
- **Reasoning tokens are invisible on Chat Completions.** The model reasons
  either way, and you are billed for it either way, but only the Responses API
  returns the reasoning content. The gateway exposes both, so use
  `/responses` when you want to see what it was thinking.
- **256K context.** Large, which makes it tempting to just keep appending.
  Resist that: the efficiency board ranks on tokens.
- **3.5 MB request payload cap**, including any images.

## Running a task locally

From the repo root:

```
just eval                       # one task, against your working tree
just eval fanout-join           # a specific task
just view                       # what your agent actually did
```

No commit needed: it mounts this directory, so the edit-run-look loop is as fast
as your agent is.

Run **one task, or a few**. You have every task, which is not an invitation to
run them all: the full suite takes hours on a laptop and tells you nothing you
could not learn from one failure. Pick a task you lose, find where your agent
goes off the rails, fix the context, run it again.

`just view` is Harbor's viewer over the local `jobs/` directory: which trials
passed, how long they took, the full trajectory turn by turn. Start there after
every run. The thing that decides your score is what ends up in the context
window, and you cannot fix what you have not looked at.

## Scoring

Two boards from one run:

- **Accuracy**: tasks passed. Ties broken by fewer tokens.
- **Efficiency**: fewest tokens, among teams passing at least half of what the
  leader passed.

You get 5 submissions of the full suite. Running tasks locally is unlimited and
unscored, so the loop above costs you nothing -- only submitting does.

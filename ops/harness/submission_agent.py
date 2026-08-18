"""Harbor agent that runs a participant's submitted commit.

One adapter serves every team. It clones their repo at a pinned commit,
installs it with uv, and invokes the entrypoint their pyproject.toml declares.
Whatever happens behind that entrypoint is theirs.

Run it with:

    harbor run -p ./tasks \
        -a harness.submission_agent:Submission \
        --ae SUBMISSION_REPO_URL=https://github.com/team/agent \
        --ae SUBMISSION_COMMIT=<sha> \
        --ae GATEWAY_URL=https://gateway.example.com/v1 \
        --ae GATEWAY_API_KEY=<per-team key>
"""

import json
import shlex
from typing import override

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

CHECKOUT_DIR = "/tmp/submission"


class Submission(BaseInstalledAgent):
    @staticmethod
    @override
    def name() -> str:
        return "submission"

    @override
    def version(self) -> str | None:
        # The commit is the only version that means anything here.
        return self._get_env("SUBMISSION_COMMIT") or "local"

    def _required(self, key: str) -> str:
        value = self._get_env(key)
        if not value:
            raise ValueError(f"{key} must be passed to the agent with --ae {key}=...")
        return value

    def _project_dir(self) -> str:
        """Where the uv project lives inside the checkout.

        A team forks the benchmark repo itself, so their agent is a directory in
        it rather than the whole thing. Empty means the project is the checkout
        root, which is what a locally mounted working tree looks like.
        """
        subdir = self._get_env("SUBMISSION_SUBDIR")
        return f"{CHECKOUT_DIR}/{subdir}" if subdir else CHECKOUT_DIR

    @property
    def _mcp_config_path(self) -> str:
        return f"{self._project_dir()}/.mcp.json"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        local_dir = self._get_env("SUBMISSION_LOCAL_DIR")

        if local_dir:
            # Local iteration: the working tree is mounted read-only, so there
            # is nothing to commit before testing a change. Everything after
            # the copy is identical to the scored path.
            fetch = f"cp -a {shlex.quote(local_dir)}/. {CHECKOUT_DIR}/"
        else:
            repo_url = self._required("SUBMISSION_REPO_URL")
            commit = self._required("SUBMISSION_COMMIT")
            # Fetching the single commit keeps clone time flat regardless of
            # how much history a team accumulated during the event.
            fetch = (
                "git init -q .; "
                f"git remote add origin {shlex.quote(repo_url)}; "
                f"git fetch -q --depth 1 origin {shlex.quote(commit)}; "
                "git checkout -q FETCH_HEAD"
            )

        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                f"rm -rf {CHECKOUT_DIR}; "
                f"mkdir -p {CHECKOUT_DIR}; "
                f"cd {CHECKOUT_DIR}; "
                f"{fetch}; "
                f"cd {self._project_dir()}; "
                "uv sync --frozen"
            ),
        )

    async def _write_mcp_config(self, environment: BaseEnvironment) -> str | None:
        """Materialise `mcp_servers:` from agent.yaml as a Claude-style .mcp.json.

        Harbor hands the declarations to the adapter and expects the adapter to
        place them. Without this, a team could declare MCP servers and silently
        get none.
        """
        if not self.mcp_servers:
            return None

        servers = {}
        for server in self.mcp_servers:
            entry = server.model_dump(exclude_none=True)
            entry.pop("name", None)
            servers[server.name] = entry

        config = json.dumps({"mcpServers": servers}, indent=2)
        await self.exec_as_agent(
            environment,
            command=f"cat > {self._mcp_config_path} <<'HARBOR_MCP_EOF'\n{config}\nHARBOR_MCP_EOF",
        )
        return self._mcp_config_path

    @override
    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        env = {
            # Harbor collects /logs into the trial directory, so a file written
            # here lands beside agent.log and the verifier output. The worker
            # converts it to the ATIF trajectory the viewer renders.
            "TRAJECTORY_PATH": "/logs/agent/messages.json",
            "GATEWAY_URL": self._required("GATEWAY_URL"),
            "GATEWAY_API_KEY": self._required("GATEWAY_API_KEY"),
            "MODEL": self.model_name or "gemma",
        }

        # Skills and MCP servers come from agent.yaml. They are surfaced as
        # env vars rather than forced into a framework, because the harness
        # behind the entrypoint is the team's choice.
        if self.skills_dir:
            env["SKILLS_DIR"] = self.skills_dir

        mcp_config = await self._write_mcp_config(environment)
        if mcp_config:
            env["MCP_CONFIG"] = mcp_config

        # Runs *in* /app, the task workspace, with --project pointing uv at the
        # submission. Starting in the submission directory instead meant every
        # relative path an agent used landed outside the tree being graded: one
        # rollout wrote a correct challenges.py next to its own pyproject.toml
        # while the verifier read the untouched stub in /app and scored zero.
        # Doing it here rather than in the baseline agent covers every harness,
        # since opencode, claude-code and the rest all act on their cwd.
        #
        # pipefail because the exit status of a pipeline is `tee`'s, and tee
        # succeeds whatever the agent did: without it a harness that died on its
        # first turn looked like a clean run, and the reason for a zero had to be
        # dug out of the log rather than read off the result.
        await self.exec_as_agent(
            environment,
            command=(
                "set -o pipefail; "
                # tee opens this before the agent runs, and nothing else has
                # created it yet on a task whose image ships no /logs/agent.
                "mkdir -p /logs/agent; "
                "cd /app && "
                f"uv run --project {self._project_dir()} "
                f"agent --instruction {shlex.quote(instruction)} "
                "2>&1 | stdbuf -oL tee /logs/agent/agent.log"
            ),
            env=env,
        )

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        # Token counts come from the gateway, keyed on the per-submission API
        # key, because anything self-reported by participant code is reporting
        # the number it is being scored on.
        pass

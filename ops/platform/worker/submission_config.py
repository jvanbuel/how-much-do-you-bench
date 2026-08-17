"""The harness a submission is graded with, taken from its own `agent.yaml`.

`agent.yaml` is the file teams are told decides what runs, and `just eval`
honours it. Grading has to honour the same file or the local loop measures a
different agent than the board does: a team iterating against opencode with a
skill directory attached was scored on `uv run agent` with neither.

So the worker fetches the submitted commit, reads its `agent.yaml`, and hands
Harbor a config built from it. What it does not do is hand Harbor the team's
file verbatim. A JobConfig can also carry `mounts`, `env` and a task path, and
this process drives the host's Docker daemon: a bind mount named in a
participant's config would be resolved on the host. Only the agent-selection
fields are copied across, into a config this module writes itself.

The clone is worker-side and shallow. It exists to resolve `skills:` paths and
to read one file -- the task container still fetches the repo itself, because
that is the checkout being graded.
"""

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

# The adapter that runs a team's own `uv run agent` entrypoint. The only import
# path a submitted config may name, because any other one has to be importable
# in *this* process, and the team's repo is not on this process's path.
SUBMISSION_ADAPTER = "harness.submission_agent:Submission"

# What a submitted agent entry may set. Everything else in Harbor's JobConfig is
# ours: the task, the repeat count, the output directory, the agent environment.
AGENT_FIELDS = ("name", "import_path", "model_name", "skills", "mcp_servers", "kwargs")

# `org/repo@ref`, Harbor's git skill source, as opposed to a path in the repo.
GIT_SOURCE = re.compile(r"^[\w.-]+/[\w.-]+@[\w./-]+$")

# Only what the API already accepts, checked again here: this URL is handed to
# git in a container holding the Docker socket, and git's `ext::` transport
# would run a command rather than fetch a repository.
REPO_URL = re.compile(r"^https://[\w.-]+(/[\w.-]+){2,5}$")

CLONE_TIMEOUT = 180


class ConfigError(Exception):
    """The submitted config cannot be graded as written."""


def fetch(repo_url: str, commit: str, dest: Path) -> None:
    """Shallow-fetch one commit into `dest`.

    Single commit rather than a clone, so fetch time is flat however much
    history a team accumulated during the event.
    """
    if not REPO_URL.fullmatch(repo_url):
        raise ConfigError(f"repo_url is not an https git URL: {repo_url}")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ConfigError("commit must be a full 40-character SHA")

    dest.mkdir(parents=True, exist_ok=True)
    # protocol.allow=never with https re-enabled: git's ext:: and file::
    # transports execute a command, and the URL comes from a submission.
    git = [
        "git",
        "-c", "protocol.allow=never",
        "-c", "protocol.https.allow=always",
        "-C", str(dest),
    ]
    steps = [
        [*git, "init", "-q"],
        [*git, "remote", "add", "origin", repo_url],
        [*git, "fetch", "-q", "--depth", "1", "origin", commit],
        [*git, "checkout", "-q", "FETCH_HEAD"],
    ]
    for step in steps:
        done = subprocess.run(step, capture_output=True, text=True, timeout=CLONE_TIMEOUT)
        if done.returncode != 0:
            raise ConfigError(f"{' '.join(step[-3:])}: {done.stderr.strip()[:300]}")


def _skill(source: str, root: Path) -> str:
    """A skill entry, with repo-relative paths made absolute.

    Harbor resolves a relative path against the working directory, which for a
    worker is the image's own /app rather than the team's checkout. Resolving
    here removes the ambiguity: what the config means by `./skills/x` is the
    directory of that name in the commit being graded.
    """
    if GIT_SOURCE.fullmatch(source):
        return source

    path = (root / source).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ConfigError(f"skill path escapes the repository: {source}")
    if not path.is_dir():
        raise ConfigError(f"skill directory not found in the commit: {source}")
    return str(path)


def _agent(entry: dict, root: Path, model: str) -> dict:
    if not isinstance(entry, dict):
        raise ConfigError("each entry under `agents:` must be a mapping")

    unknown = sorted(set(entry) - set(AGENT_FIELDS))
    if unknown:
        raise ConfigError(
            f"{', '.join(unknown)} cannot be set on a graded run "
            f"(allowed: {', '.join(AGENT_FIELDS)})"
        )

    clean: dict[str, Any] = {}

    # Either or both: an off-the-shelf harness is a `name`, a team's own code is
    # an `import_path`, and the template pairs the adapter with a name to label
    # it. What matters is that at least one of them says what to run.
    name, import_path = entry.get("name"), entry.get("import_path")
    if not name and not import_path:
        raise ConfigError("an agent needs a `name` or an `import_path`")
    if import_path and import_path != SUBMISSION_ADAPTER:
        raise ConfigError(
            f"import_path must be {SUBMISSION_ADAPTER} -- a path into your own "
            "repository is not importable by the runner"
        )
    if name and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", str(name)):
        raise ConfigError(f"unusable agent name: {name}")
    if name:
        clean["name"] = name
    if import_path:
        clean["import_path"] = import_path

    # One model is the point of the event. A provider prefix is allowed because
    # that is how some harnesses name things -- opencode wants `openai/gemma`.
    model_name = entry.get("model_name") or model
    if not re.fullmatch(rf"([a-z0-9_.-]+/)?{re.escape(model)}", str(model_name)):
        raise ConfigError(f"model_name must be {model} or <provider>/{model}, not {model_name}")
    clean["model_name"] = model_name

    skills = entry.get("skills") or []
    if not isinstance(skills, list):
        raise ConfigError("`skills:` must be a list")
    clean["skills"] = [_skill(str(s), root) for s in skills]

    servers = entry.get("mcp_servers") or []
    if not isinstance(servers, list) or any(not isinstance(s, dict) for s in servers):
        raise ConfigError("`mcp_servers:` must be a list of mappings")
    clean["mcp_servers"] = servers

    kwargs = entry.get("kwargs") or {}
    if not isinstance(kwargs, dict):
        raise ConfigError("`kwargs:` must be a mapping")
    clean["kwargs"] = kwargs

    return clean


def build(root: Path, model: str) -> tuple[Path, str]:
    """Write the config this rollout is graded with, and name the agent.

    Only the first entry under `agents:` is graded. Listing several is a local
    comparison tool -- two agents in one scored run would be two results
    competing for one row, keyed the same way.
    """
    source = root / "agent.yaml"
    if not source.is_file():
        raise ConfigError("no agent.yaml in the submitted commit")

    try:
        document = yaml.safe_load(source.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"agent.yaml is not valid YAML: {exc}") from exc

    agents = document.get("agents") if isinstance(document, dict) else None
    if not isinstance(agents, list) or not agents:
        raise ConfigError("agent.yaml has no `agents:` list")

    entry = _agent(agents[0], root, model)
    graded = root / ".graded-agent.yaml"
    graded.write_text(yaml.safe_dump({"agents": [entry]}, sort_keys=False))
    return graded, entry.get("name") or "submission"


def resolve(job: dict, workdir: Path, model: str) -> tuple[Path | None, str, str | None]:
    """The config to grade `job` with: (config path, agent label, note).

    A config that cannot be graded is not a failed rollout. The team's own
    entrypoint is still the thing being scored, so the run falls back to the
    submission adapter and the reason travels with the result, where the team
    can read it on the board.
    """
    try:
        fetch(job["repo_url"], job["commit"], workdir)
        config, agent = build(workdir, model)
        return config, agent, None
    except ConfigError as exc:
        return None, "submission", f"agent.yaml ignored: {exc}"
    except Exception as exc:  # noqa: BLE001
        return None, "submission", f"agent.yaml ignored: {type(exc).__name__}: {exc}"


def _demo() -> None:
    """The sanitiser is the security boundary, so it is checked rather than
    trusted: this process holds the Docker socket, and a mount or an env
    override in a submitted config would be honoured by the host daemon."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "skills" / "de").mkdir(parents=True)

        entry = _agent(
            {"name": "opencode", "model_name": "openai/gemma", "skills": ["./skills/de"]},
            root,
            "gemma",
        )
        assert entry["name"] == "opencode"
        # The relative path became this commit's directory, not the runner's.
        assert entry["skills"] == [str((root / "skills" / "de").resolve())]

        def rejects(entry_dict, fragment):
            try:
                _agent(entry_dict, root, "gemma")
            except ConfigError as exc:
                assert fragment in str(exc), f"{fragment!r} not in {exc}"
                return
            raise AssertionError(f"accepted a config it should refuse: {entry_dict}")

        # A host bind mount, resolved by the daemon this process drives.
        rejects({"name": "opencode", "mounts": [{"source": "/", "target": "/host"}]}, "mounts")
        # Repointing the gateway would take the meter off the model call.
        rejects({"name": "opencode", "env": {"GATEWAY_URL": "https://elsewhere"}}, "env")
        # One model for everyone is the premise of the event.
        rejects({"name": "opencode", "model_name": "openai/gpt-5"}, "model_name must be")
        # Not importable in this process, so it would fail the rollout instead.
        rejects({"import_path": "agent.main:Mine"}, "import_path must be")
        rejects({"name": "opencode", "skills": ["../../etc"]}, "escapes the repository")
        rejects({"name": "opencode", "skills": ["./skills/missing"]}, "not found")
        rejects({}, "needs a `name`")

        # The template pairs the adapter with a name to label it, which is what
        # a team gets by uncommenting the block in agent.yaml. Both is fine.
        paired = _agent(
            {"name": "submission", "import_path": SUBMISSION_ADAPTER, "model_name": "gemma"},
            root,
            "gemma",
        )
        assert paired["import_path"] == SUBMISSION_ADAPTER and paired["name"] == "submission"

        # A whole file, end to end.
        (root / "agent.yaml").write_text(
            "agents:\n"
            "  - name: opencode\n"
            "    model_name: openai/gemma\n"
            "    skills: [./skills/de]\n"
            "  - name: aider\n"  # a second entry is a local comparison, not a second score
        )
        config, agent = build(root, "gemma")
        assert agent == "opencode"
        written = yaml.safe_load(config.read_text())
        assert len(written["agents"]) == 1, "only the first agent is graded"

        (root / "agent.yaml").write_text("agents: []\n")
        try:
            build(root, "gemma")
            raise AssertionError("accepted an empty agents list")
        except ConfigError:
            pass

    # A URL git would execute rather than fetch.
    for bad in ("ext::sh -c whoami", "file:///etc", "git@github.com:t/a.git"):
        assert not REPO_URL.fullmatch(bad), bad
    assert REPO_URL.fullmatch("https://github.com/team/agent")
    assert REPO_URL.fullmatch("https://github.com/team/agent.git")

    print("submission config ok")


if __name__ == "__main__":
    _demo()

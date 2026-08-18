"""Where a submission may be fetched from.

Shared because it was duplicated and the two copies disagreed: the API enforced
a host allowlist, the worker's own regex did not, and the worker's comment said
it was "only what the API already accepts". The worker is the process that hands
this URL to git while holding the Docker socket, so it is the copy that mattered.
"""

import os
import re

# github.com unless told otherwise. Teams fork this repository, so the default
# is the only host a submission legitimately comes from.
HOSTS = tuple(h for h in os.environ.get("REPO_HOSTS", "github.com").split(",") if h)

# An https URL with a path, and nothing that git would read as a transport:
# `ext::` runs a command rather than fetching a repository.
PATTERN = re.compile(r"https://([\w.-]+)((?:/[\w.-]+){2,5})")


def normalise(url: str) -> str:
    """The canonical form of a submission URL, or ValueError saying what is wrong."""
    cleaned = url.strip().removesuffix(".git")
    match = PATTERN.fullmatch(cleaned)
    if not match:
        raise ValueError(f"repo_url must be an https git URL, not {url!r}")
    if match.group(1) not in HOSTS:
        raise ValueError(f"repo_url must be hosted on {' or '.join(HOSTS)}, not {match.group(1)}")
    return cleaned


def _demo() -> None:
    assert normalise("https://github.com/team/repo.git") == "https://github.com/team/repo"
    for bad in (
        "http://github.com/team/repo",            # not https
        "https://evil.example/team/repo",         # not an allowed host
        "ext::sh -c whoami",                      # git transport, not a URL
        "https://github.com/team",                # no repository
    ):
        try:
            normalise(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")
    print("ok")


if __name__ == "__main__":
    _demo()

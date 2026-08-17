"""Run `tofu validate` (or `terraform validate`) against a Terraform workspace.

This is the *real* validity check for the Terraform exercises: it confirms the
answer is not just structurally plausible but valid HCL the provider accepts
(correct resource/argument names, types, etc.).

`validate(target_dir)` copies the workspace's ``*.tf`` into a scratch dir, runs
``init -backend=false`` then ``validate``, and returns ``(ok, output)``.

Provider acquisition is expected to be served offline by a filesystem mirror
configured through ``$TF_CLI_CONFIG_FILE`` (the benchmark's Docker image sets
this up). When no `tofu`/`terraform` binary is on PATH, or when providers cannot
be installed (no mirror and no network), callers should *skip* rather than fail:
use `available()` and inspect the returned output for an install failure.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# Substrings that mean "couldn't get providers" (an environment problem), as
# opposed to a genuine validation error in the candidate's config.
_PROVIDER_INSTALL_ERRORS = (
    "Failed to install provider",
    "could not query provider",
    "no available releases",
    "Failed to query available provider packages",
    "registry.opentofu.org",
    "registry.terraform.io",
    "Could not retrieve the list of available versions",
)


def binary() -> str | None:
    """Return the path to a usable IaC binary, honouring $TF_BIN."""
    env = os.environ.get("TF_BIN")
    if env and shutil.which(env):
        return shutil.which(env)
    return shutil.which("tofu") or shutil.which("terraform")


def available() -> bool:
    return binary() is not None


def validate(target_dir, timeout: int = 180) -> tuple[bool, str]:
    """Return (is_valid, combined_output). Raises if no binary is available."""
    tf = binary()
    if tf is None:
        raise RuntimeError("no tofu/terraform binary on PATH")

    target = Path(target_dir)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for f in target.glob("*.tf"):
            shutil.copy2(f, work / f.name)
        # Also carry asset subdirectories (e.g. templates/ referenced by
        # `templatefile("${path.module}/templates/...")`) so validation resolves
        # them. Only root *.tf is the module (tofu reads the root
        # non-recursively), so nested *.tf under these dirs stays ignored.
        for d in target.iterdir():
            if d.is_dir() and d.name != ".terraform" and not d.name.startswith("."):
                shutil.copytree(d, work / d.name)

        def run(*args):
            return subprocess.run([tf, *args], cwd=work, capture_output=True,
                                  text=True, timeout=timeout)

        init = run("init", "-backend=false", "-input=false", "-no-color")
        if init.returncode != 0:
            return False, init.stdout + init.stderr

        val = run("validate", "-no-color")
        return val.returncode == 0, val.stdout + val.stderr


def is_provider_install_failure(output: str) -> bool:
    return any(s in output for s in _PROVIDER_INSTALL_ERRORS)

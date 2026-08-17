"""Terraform grader bootstrap: expose the parsed HCL of the target workspace."""
import os
import sys
from pathlib import Path

import pytest

# grader_lib sits at a different depth in-repo (custom-benchmark/grader_lib) than
# in a generated Harbor task (tests/grader_lib), so walk up until we find it
# instead of hardcoding a parents[] index.
def _find_grader_lib() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "grader_lib"
        if candidate.is_dir():
            return candidate
    raise RuntimeError("grader_lib not found above " + __file__)


sys.path.insert(0, str(_find_grader_lib()))


def _target_dir() -> Path:
    env = os.environ.get("BENCH_TARGET")
    if env:
        return Path(env).resolve()
    return (Path(__file__).resolve().parent.parent / "workspace").resolve()


@pytest.fixture(scope="session")
def target_dir() -> Path:
    return _target_dir()


@pytest.fixture(scope="session")
def tf_text(target_dir) -> str:
    import hcl_lite
    return hcl_lite.load_tf(target_dir)

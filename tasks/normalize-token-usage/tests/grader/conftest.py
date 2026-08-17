"""Make the model's answer importable and locate its sample files.

Grades the directory named by $BENCH_TARGET, defaulting to the sibling
workspace/. The directory is put on sys.path so `import normalize_usage`
resolves to the candidate file there.
"""
import os
import sys
from pathlib import Path

import pytest


def _target_dir() -> Path:
    env = os.environ.get("BENCH_TARGET")
    if env:
        return Path(env).resolve()
    return (Path(__file__).resolve().parent.parent / "workspace").resolve()


@pytest.fixture(scope="session")
def target_dir() -> Path:
    return _target_dir()


def pytest_configure():
    sys.path.insert(0, str(_target_dir()))

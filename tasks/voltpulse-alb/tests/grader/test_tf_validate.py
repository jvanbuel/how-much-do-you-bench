"""Validity check: the answer must pass `tofu validate` (real provider schema
check). Skipped when no tofu/terraform binary is present, or when providers
cannot be installed offline -- the benchmark container provides both.
"""
import pytest

import tf_validate


def test_tofu_validate(target_dir):
    if not tf_validate.available():
        pytest.skip("no tofu/terraform binary on PATH "
                    "(run inside the benchmark container)")
    ok, output = tf_validate.validate(target_dir)
    if not ok and tf_validate.is_provider_install_failure(output):
        pytest.skip("providers could not be installed offline:\n" + output)
    assert ok, f"tofu validate failed:\n{output}"

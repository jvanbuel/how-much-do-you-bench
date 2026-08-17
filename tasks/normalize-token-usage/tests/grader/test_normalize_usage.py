"""Hidden grader for exercise 09: normalize heterogeneous usage exports."""
import importlib

import pytest


@pytest.fixture(scope="session")
def mod():
    return importlib.import_module("normalize_usage")


@pytest.fixture
def samples(target_dir):
    return target_dir / "samples"


def test_claude_json(mod, samples):
    rows = mod.normalize_usage(samples / "claude.json")
    assert rows == [
        {"date": "2026-01-02", "input_tokens": 1000, "output_tokens": 200,
         "cache_read_tokens": 5000, "cost_usd": pytest.approx(0.12),
         "source": "claude-json"},
        {"date": "2026-01-03", "input_tokens": 2000, "output_tokens": 50,
         "cache_read_tokens": 0, "cost_usd": pytest.approx(0.03),
         "source": "claude-json"},
    ]


def test_cursor_csv_aggregates_per_day(mod, samples):
    rows = mod.normalize_usage(samples / "cursor.csv")
    assert rows == [
        {"date": "2026-03-10", "input_tokens": 300, "output_tokens": 30,
         "cache_read_tokens": 1000, "cost_usd": pytest.approx(0.05),
         "source": "cursor-csv"},
        {"date": "2026-03-11", "input_tokens": 300, "output_tokens": 30,
         "cache_read_tokens": 0, "cost_usd": pytest.approx(0.0),
         "source": "cursor-csv"},
    ]


def test_cli_table(mod, samples):
    rows = mod.normalize_usage(samples / "cli-table.txt")
    assert rows == [
        {"date": "2025-11-14", "input_tokens": 1234, "output_tokens": 56,
         "cache_read_tokens": 0, "cost_usd": pytest.approx(0.01),
         "source": "cli-table"},
        {"date": "2025-11-15", "input_tokens": 10000, "output_tokens": 500,
         "cache_read_tokens": 2000, "cost_usd": pytest.approx(0.40),
         "source": "cli-table"},
    ]


def test_sorted_by_date(mod, samples):
    for name in ("claude.json", "cursor.csv", "cli-table.txt"):
        rows = mod.normalize_usage(samples / name)
        assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)

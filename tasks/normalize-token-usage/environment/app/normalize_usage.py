"""Normalize heterogeneous token-usage exports into one schema.

`normalize_usage(path)` detects the format from the file, parses it, and
returns one record per day, sorted by date:

    {
        "date": "YYYY-MM-DD",
        "input_tokens": ...,
        "output_tokens": ...,
        "cache_read_tokens": ...,
        "cost_usd": ...,
        "source": ...,            # "claude-json" | "cursor-csv" | "cli-table"
    }

A format that reports several rows for one day is totalled into that day's
record.
"""
from __future__ import annotations

from pathlib import Path


def normalize_usage(path) -> list[dict]:
    raise NotImplementedError

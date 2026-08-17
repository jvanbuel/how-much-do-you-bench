#!/bin/bash
# The upstream reference answer, embedded file by file.
set -euo pipefail

mkdir -p /app
cat > /app/normalize_usage.py <<'SOLUTION_EOF'
"""Reference solution: normalize heterogeneous token-usage exports."""
from __future__ import annotations

import csv
import json
from pathlib import Path


def _to_int(value) -> int:
    """Parse a token count, tolerating thousands separators and blanks."""
    s = str(value).replace(",", "").strip()
    return int(s) if s else 0


def _to_money(value) -> float:
    """Parse a cost cell: '$0.40', '0.05', '' or 'Included' (-> 0.0)."""
    s = str(value).strip()
    if not s or s.lower() in {"included", "n/a", "-"}:
        return 0.0
    s = s.replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _from_claude_json(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    rows = []
    for day in data.get("daily", []):
        rows.append({
            "date": day["period"],
            "input_tokens": int(day.get("inputTokens", 0)),
            "output_tokens": int(day.get("outputTokens", 0)),
            "cache_read_tokens": int(day.get("cacheReadTokens", 0)),
            "cost_usd": float(day.get("totalCost", 0.0)),
            "source": "claude-json",
        })
    return rows


def _from_cursor_csv(path: Path) -> list[dict]:
    by_day: dict[str, dict] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            date = row["Date"][:10]            # ISO timestamp -> YYYY-MM-DD
            acc = by_day.setdefault(date, {
                "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cost_usd": 0.0,
            })
            acc["input_tokens"] += _to_int(row.get("Input (w/o Cache Write)"))
            acc["output_tokens"] += _to_int(row.get("Output Tokens"))
            acc["cache_read_tokens"] += _to_int(row.get("Cache Read"))
            acc["cost_usd"] += _to_money(row.get("Cost"))
    return [{"date": d, "source": "cursor-csv", **vals} for d, vals in by_day.items()]


def _from_cli_table(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        if "│" not in line:
            continue
        cells = [c.strip() for c in line.strip().strip("│").split("│")]
        date = cells[0]
        if not (len(date) == 10 and date[4] == "-" and date[7] == "-"):
            continue  # header / separator rows
        # Date, Agent, Models, Input, Output, Cache Create, Cache Read, Total, Cost
        rows.append({
            "date": date,
            "input_tokens": _to_int(cells[3]),
            "output_tokens": _to_int(cells[4]),
            "cache_read_tokens": _to_int(cells[6]),
            "cost_usd": _to_money(cells[8]),
            "source": "cli-table",
        })
    return rows


def normalize_usage(path) -> list[dict]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        rows = _from_claude_json(path)
    elif suffix == ".csv":
        rows = _from_cursor_csv(path)
    else:
        rows = _from_cli_table(path)
    rows.sort(key=lambda r: r["date"])
    return rows
SOLUTION_EOF

# Normalize token-usage exports

A few teammates exported their AI coding-tool usage in three different formats,
and I want them all in one shape so I can total them up.

`samples/` contains exports from different tools in different formats:

- `claude.json`: a Claude/Gemini-style export.
- `cursor.csv`: a Cursor export
- `cli-table.txt`: a CLI table

In `normalize_usage.py`, implement `normalize_usage(path)`: detect the format
from the file, parse it, and return one record per day, sorted by date:

```python
{
    "date": "YYYY-MM-DD",
    "input_tokens": ...,
    "output_tokens": ...,
    "cache_read_tokens": ...,
    "cost_usd": ...,
    "source": ...,            # "claude-json" | "cursor-csv" | "cli-table"
}
```
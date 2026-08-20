# Normalize token-usage exports

A few teammates exported their AI coding-tool usage in three different formats,
and I want them all in one shape so I can total them up.

`samples/` has one export from each tool: a Claude/Gemini-style JSON, a Cursor
CSV, and a CLI table.

`normalize_usage.py` has the function to implement and the exact record shape
it has to return.

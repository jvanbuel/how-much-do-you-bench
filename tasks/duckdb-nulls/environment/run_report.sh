#!/bin/bash
# Splits report.sql on its section markers and writes one parquet per section.
set -euo pipefail
mkdir -p /app/out

python - <<'PY'
import re
from pathlib import Path

import duckdb

sql = Path("/app/report.sql").read_text()
sections = re.findall(r"^-- (revenue_by_month|dormant_customers)\s*$(.*?)(?=^-- \w|\Z)",
                      sql, re.M | re.S)
con = duckdb.connect("/app/shop.duckdb", read_only=True)
for name, body in sections:
    body = body.strip()
    if not body:
        continue
    con.execute(f"COPY ({body.rstrip(';')}) TO '/app/out/{name}.parquet' (FORMAT parquet)")
con.close()
PY

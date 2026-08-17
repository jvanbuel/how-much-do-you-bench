"""Both result sets are recomputed here from the warehouse, independently."""

import subprocess
import sys
from pathlib import Path

import duckdb
import polars as pl

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {label}" + ("" if ok else f": {detail}"))
    if not ok:
        failures.append(label)


for stale in Path("/app/out").glob("*.parquet"):
    stale.unlink()

run = subprocess.run(["bash", "/app/run_report.sh"], capture_output=True, text=True, timeout=600)
check("report runs", run.returncode == 0, (run.stderr or run.stdout)[-400:])

con = duckdb.connect("/app/shop.duckdb", read_only=True)

want_revenue = con.execute("""
    SELECT strftime(ordered_at, '%Y-%m') AS period,
           round(sum(amount), 2) AS revenue
    FROM orders GROUP BY 1 ORDER BY 1
""").pl()

# Dormant means no order in the last 90 days. Country is irrelevant, and a NULL
# country must not exclude anyone.
want_dormant = con.execute("""
    SELECT c.customer_id
    FROM customers c
    WHERE NOT EXISTS (
      SELECT 1 FROM orders o
      WHERE o.customer_id = c.customer_id
        AND o.ordered_at >= (SELECT max(ordered_at) FROM orders) - INTERVAL 90 DAY
    )
    ORDER BY 1
""").pl()
con.close()

rev_path = Path("/app/out/revenue_by_month.parquet")
dor_path = Path("/app/out/dormant_customers.parquet")

if not rev_path.exists() or not dor_path.exists():
    check("both result sets written", False, f"{rev_path.exists()=} {dor_path.exists()=}")
    sys.exit(1)

revenue = pl.read_parquet(rev_path)
dormant = pl.read_parquet(dor_path)

check(
    "one period per calendar month",
    revenue.height == want_revenue.height,
    f"{revenue.height} periods against {want_revenue.height}",
)
check(
    "revenue total",
    abs(revenue["revenue"].sum() - want_revenue["revenue"].sum()) < 0.5,
    f"{revenue['revenue'].sum():.2f} vs {want_revenue['revenue'].sum():.2f}",
)
if revenue.height == want_revenue.height:
    worst = (revenue["revenue"].sort() - want_revenue["revenue"].sort()).abs().max()
    check("revenue per period", worst < 0.5, f"largest gap {worst:.2f}")

check("dormant customers found", dormant.height > 0, "still returns nothing")
check(
    "dormant list matches",
    sorted(dormant["customer_id"].to_list()) == sorted(want_dormant["customer_id"].to_list()),
    f"{dormant.height} rows against {want_dormant.height}",
)

sys.exit(1 if failures else 0)

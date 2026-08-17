"""Verifies fct_orders against truth derived from the raw feed.

Expectations are computed here rather than hardcoded, so the feed can change
size without the assertions rotting. The reference implementation lives in the
test because /tests is copied in at verification time, after the agent is gone.

The pipeline is replayed from clean by test.sh before these run, so this checks
what the pipeline produces, not what happens to be in the database when the
agent stops.
"""

import csv
import hashlib
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

RAW_CSV = Path("/app/data/raw_orders.csv")
# The verifier's own copy: regenerating from the agent's copy only catches
# editing one of the two files, and an agent that rewrites the generator and the
# feed together gets a feed with no duplicates to deduplicate.
GENERATOR = Path("/tests/generate_data.py")
WAREHOUSE = "/app/warehouse.duckdb"


def _expected() -> dict[int, Decimal]:
    """One row per order, carrying the newest amount by updated_at."""
    newest: dict[int, tuple[str, Decimal]] = {}
    with RAW_CSV.open() as handle:
        for row in csv.DictReader(handle):
            order_id = int(row["order_id"])
            updated_at = row["updated_at"]
            amount = Decimal(row["amount"])
            if order_id not in newest or updated_at > newest[order_id][0]:
                newest[order_id] = (updated_at, amount)
    return {order_id: amount for order_id, (_, amount) in newest.items()}


@pytest.fixture(scope="module")
def con():
    connection = duckdb.connect(WAREHOUSE, read_only=True)
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def expected():
    return _expected()


def test_raw_feed_untouched():
    """Regenerate from the same seed and compare bytes.

    Stronger than a hardcoded checksum, which would have to be updated
    whenever the generator changes, and catches edits to either file.
    """
    assert RAW_CSV.is_file(), "raw_orders.csv is missing"
    assert GENERATOR.is_file(), "the verifier's generate_data.py is missing"

    scratch = Path("/tmp/raw_orders_check.csv")
    subprocess.run(
        [sys.executable, str(GENERATOR)],
        env={**os.environ, "RAW_OUT": str(scratch)},
        check=True,
        capture_output=True,
    )

    actual = hashlib.sha256(RAW_CSV.read_bytes()).hexdigest()
    reference = hashlib.sha256(scratch.read_bytes()).hexdigest()
    assert actual == reference, "the raw feed does not match what the generator produces"


def test_one_row_per_order(con):
    total, distinct = con.execute(
        "select count(*), count(distinct order_id) from fct_orders"
    ).fetchone()
    assert total == distinct, f"{total - distinct} duplicate row(s) in fct_orders"


def test_every_order_present(con, expected):
    found = {row[0] for row in con.execute("select order_id from fct_orders").fetchall()}
    missing = set(expected) - found
    assert not missing, f"{len(missing)} order(s) missing, e.g. {sorted(missing)[:5]}"


def test_no_unknown_orders(con, expected):
    found = {row[0] for row in con.execute("select order_id from fct_orders").fetchall()}
    unknown = found - set(expected)
    assert not unknown, f"{len(unknown)} order(s) not in the raw feed"


def test_amounts_are_the_latest_restatement(con, expected):
    rows = con.execute("select order_id, amount from fct_orders").fetchall()
    actual = {order_id: Decimal(str(amount)) for order_id, amount in rows}
    wrong = {k: (actual[k], expected[k]) for k in actual if actual[k] != expected[k]}
    assert not wrong, f"{len(wrong)} order(s) carry a stale amount, e.g. {list(wrong.items())[:3]}"


def test_total_revenue(con, expected):
    (total,) = con.execute("select sum(amount) from fct_orders").fetchone()
    assert Decimal(str(total)) == sum(expected.values())


def test_daily_revenue_agrees_with_fct_orders(con):
    # The downstream mart must not have been fixed independently of its source.
    mismatch = con.execute(
        """
        with truth as (
            select order_date, count(*) as n, sum(amount) as revenue
            from fct_orders group by order_date
        )
        select count(*) from truth
        join fct_daily_revenue using (order_date)
        where truth.n != fct_daily_revenue.order_count
           or truth.revenue != fct_daily_revenue.revenue
        """
    ).fetchone()[0]
    assert mismatch == 0, "fct_daily_revenue disagrees with fct_orders"

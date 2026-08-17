"""Generate the raw orders feed at image build time.

Deterministic: same seed, same bytes, every build. The feed is large enough
that reading it is useless, which is the point. The previous six-row fixture
made both anomalies visible in a single `cat`, and the baseline agent solved
the task 5 times out of 5.

Two things hide in here, each about one percent of rows:

- restatements: a later batch carries a corrected amount for an existing
  order, with a NEWER updated_at
- late arrivals: an order lands in a later batch carrying an OLDER updated_at
  than rows already loaded

A pipeline that watermarks on updated_at duplicates the first and drops the
second. Neither is visible without aggregating.
"""

import csv
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

SEED = 20260810
# Each batch is a full dbt run, and the verifier replays them all, so this
# trades feed size against rollout wall-clock.
BATCHES = 10
ORDERS_PER_BATCH = 3000
RESTATEMENT_RATE = 0.015
LATE_RATE = 0.010

START = datetime(2026, 1, 1)
# Overridable so the verifier can regenerate to a scratch path and compare.
OUT = Path(os.environ.get("RAW_OUT", "/app/data/raw_orders.csv"))

FIELDS = [
    "order_id",
    "customer_id",
    "order_date",
    "amount",
    "updated_at",
    "ingested_at",
    "batch_id",
]


def money(rng: random.Random) -> str:
    return f"{rng.uniform(5, 900):.2f}"


def main() -> None:
    rng = random.Random(SEED)
    rows: list[dict] = []
    by_id: dict[int, dict] = {}
    settled: list[int] = []
    next_order_id = 100_000

    for batch in range(1, BATCHES + 1):
        day = START + timedelta(days=batch - 1)
        ingested_at = day.replace(hour=23)

        for _ in range(ORDERS_PER_BATCH):
            order_id = next_order_id
            next_order_id += 1
            updated_at = day + timedelta(minutes=rng.randint(0, 900))
            row = {
                "order_id": order_id,
                "customer_id": f"C{rng.randint(1, 4000):05d}",
                "order_date": day.date().isoformat(),
                "amount": money(rng),
                "updated_at": updated_at.isoformat(),
                "ingested_at": ingested_at.isoformat(),
                "batch_id": batch,
            }
            rows.append(row)
            by_id[order_id] = row
            settled.append(order_id)

        if batch == 1:
            continue

        # Restatements: corrected amount, newer updated_at. An append-only
        # incremental model keeps both rows and double counts the order.
        for order_id in rng.sample(settled, int(len(settled) * RESTATEMENT_RATE)):
            original = by_id[order_id]
            rows.append(
                {
                    **original,
                    "amount": money(rng),
                    "updated_at": (day + timedelta(minutes=rng.randint(0, 900))).isoformat(),
                    "ingested_at": ingested_at.isoformat(),
                    "batch_id": batch,
                }
            )

        # Late arrivals: the order was updated days ago but only reaches us
        # now. Its updated_at sits behind the watermark, so a model filtering
        # on updated_at never sees it.
        for _ in range(int(ORDERS_PER_BATCH * LATE_RATE)):
            order_id = next_order_id
            next_order_id += 1
            stale_day = START + timedelta(days=rng.randint(0, batch - 2))
            rows.append(
                {
                    "order_id": order_id,
                    "customer_id": f"C{rng.randint(1, 4000):05d}",
                    "order_date": stale_day.date().isoformat(),
                    "amount": money(rng),
                    "updated_at": (stale_day + timedelta(minutes=rng.randint(0, 900))).isoformat(),
                    "ingested_at": ingested_at.isoformat(),
                    "batch_id": batch,
                }
            )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"{len(rows)} rows -> {OUT}")


if __name__ == "__main__":
    main()

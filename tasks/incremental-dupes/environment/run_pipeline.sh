#!/bin/bash
# Rebuilds the warehouse from scratch by replaying every daily ingest batch.
set -euo pipefail

cd /app
rm -f /app/warehouse.duckdb

BATCHES=$(python3 -c "
import csv
with open('/app/data/raw_orders.csv') as f:
    print(max(int(r['batch_id']) for r in csv.DictReader(f)))
")

for BATCH in $(seq 1 "$BATCHES"); do
  python3 -c "
import duckdb
con = duckdb.connect('/app/warehouse.duckdb')
con.execute('''
    create or replace view raw_orders as
    select * from read_csv_auto('/app/data/raw_orders.csv')
    where batch_id <= $BATCH
''')
con.close()
"
  (cd /app/pipeline && dbt run --profiles-dir . --quiet)
done

echo "--- replayed $BATCHES batches"

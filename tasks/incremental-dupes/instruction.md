Finance reconciled our orders mart against the raw feed and the numbers do not
agree. They will not tell us more than that, because they do not know more than
that.

`/app/run_pipeline.sh` rebuilds the warehouse from scratch by replaying the
daily ingest batches through the dbt project in `/app/pipeline`. Each batch
loads everything that had landed in the raw feed by that day.

`fct_orders` is supposed to hold **exactly one row per order, carrying that
order's most recent amount**, and it must include every order present in the
raw feed by the final batch.

Work out how it disagrees with the raw feed, find the cause, and fix it.

Constraints:

- Do not modify `/app/data/raw_orders.csv` or `/app/generate_data.py`.
- `/app/run_pipeline.sh` must still rebuild the warehouse from scratch when
  run. It will be run from a clean state to check your work, so writing the
  expected rows into the database by hand will not help.

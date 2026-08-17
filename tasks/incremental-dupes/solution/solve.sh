#!/bin/bash
# Reference solution.
#
# Two symptoms, one root cause: the incremental filter watermarks on
# updated_at, which is a property of the order rather than of the ingest. A
# restatement with a newer updated_at gets appended alongside the original
# (revenue overstated), and a late-arriving order whose updated_at predates the
# watermark is skipped entirely (orders missing).
#
# The fix watermarks on ingested_at, dedupes to the newest row per order, and
# upserts on order_id so restatements replace rather than accumulate.
set -euo pipefail

cat > /app/pipeline/models/fct_orders.sql <<'SQL'
{{
    config(
        materialized='incremental',
        unique_key='order_id',
        incremental_strategy='delete+insert',
    )
}}

with new_rows as (

    select * from {{ ref('stg_orders') }}

    {% if is_incremental() %}
    where ingested_at > (select max(ingested_at) from {{ this }})
    {% endif %}

),

ranked as (

    select
        *,
        row_number() over (
            partition by order_id order by updated_at desc
        ) as rn
    from new_rows

)

select
    order_id,
    customer_id,
    order_date,
    amount,
    updated_at,
    ingested_at
from ranked
where rn = 1
SQL

bash /app/run_pipeline.sh

{{ config(materialized='view') }}

select
    order_id,
    customer_id,
    cast(order_date as date) as order_date,
    cast(amount as decimal(12, 2)) as amount,
    cast(updated_at as timestamp) as updated_at,
    cast(ingested_at as timestamp) as ingested_at,
    batch_id
from raw_orders

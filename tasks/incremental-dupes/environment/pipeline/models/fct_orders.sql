{{ config(materialized='incremental') }}

select
    order_id,
    customer_id,
    order_date,
    amount,
    updated_at
from {{ ref('stg_orders') }}

{% if is_incremental() %}
where updated_at > (select max(updated_at) from {{ this }})
{% endif %}

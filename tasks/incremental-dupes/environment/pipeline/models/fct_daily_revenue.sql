{{ config(materialized='table') }}

select
    order_date,
    count(*) as order_count,
    sum(amount) as revenue
from {{ ref('fct_orders') }}
group by order_date
order by order_date

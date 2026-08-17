#!/bin/bash
# month() collapses years together, so the period needs the year too. And NOT IN
# against a subquery that can yield NULL is never true for anything -- the join to
# customers pulled NULL countries into the list. NOT EXISTS says what was meant.
set -euo pipefail

cat > /app/report.sql <<'SQL'
-- Monthly revenue, and the customers we have not seen in the last 90 days.

-- revenue_by_month
SELECT
  strftime(ordered_at, '%Y-%m') AS period,
  round(sum(amount), 2) AS revenue,
  count(DISTINCT customer_id) AS customers
FROM orders
GROUP BY 1
ORDER BY 1;

-- dormant_customers
SELECT c.customer_id, c.name
FROM customers c
WHERE NOT EXISTS (
  SELECT 1
  FROM orders o
  WHERE o.customer_id = c.customer_id
    AND o.ordered_at >= (SELECT max(ordered_at) FROM orders) - INTERVAL 90 DAY
)
ORDER BY c.customer_id;
SQL

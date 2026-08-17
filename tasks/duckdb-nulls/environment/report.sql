-- Monthly revenue, and the customers we have not seen in the last 90 days.
--
-- Written against /app/shop.duckdb. `bash /app/run_report.sh` executes this and
-- writes the two result sets to /app/out/.

-- revenue_by_month
SELECT
  month(ordered_at) AS period,
  round(sum(amount), 2) AS revenue,
  count(DISTINCT customer_id) AS customers
FROM orders
GROUP BY 1
ORDER BY 1;

-- dormant_customers
SELECT c.customer_id, c.name
FROM customers c
WHERE c.customer_id NOT IN (
  SELECT o.customer_id
  FROM orders o
  WHERE o.ordered_at >= (SELECT max(ordered_at) FROM orders) - INTERVAL 90 DAY
)
ORDER BY c.customer_id;

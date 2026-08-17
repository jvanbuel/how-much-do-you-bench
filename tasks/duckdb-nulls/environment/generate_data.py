"""Builds the warehouse at image build time.

Shaped deliberately: most customers ordered recently, 300 have not ordered in
months, and some of those dormant ones have an unknown country -- which is what
makes the NULL handling in the report observable rather than theoretical.
"""

import duckdb

con = duckdb.connect("/app/shop.duckdb")
con.execute("""
CREATE TABLE customers AS
SELECT
  i AS customer_id,
  'cust-' || i AS name,
  CASE WHEN i % 40 = 0 THEN NULL ELSE 'BE' END AS country
FROM range(1, 2001) t(i);

-- Active customers: an order in the last few weeks, and history before that.
CREATE TABLE orders AS
SELECT
  i AS order_id,
  1 + (i % 1700) AS customer_id,
  (TIMESTAMP '2026-11-01 00:00:00' - INTERVAL (i % 40) DAY) AS ordered_at,
  round(5 + (i % 90) * 1.37, 2) AS amount
FROM range(1, 12001) t(i);

-- The same customers, a year and a half of history spanning the year boundary.
INSERT INTO orders
SELECT 50000 + i, 1 + (i % 1700),
       TIMESTAMP '2025-06-01 00:00:00' + INTERVAL (i % 500) DAY,
       round(4 + (i % 70) * 1.11, 2)
FROM range(1, 12001) t(i);

-- Dormant customers 1701-2000: nothing since early 2026.
INSERT INTO orders
SELECT 80000 + i, 1700 + 1 + (i % 300),
       TIMESTAMP '2026-01-05 00:00:00' + INTERVAL (i % 30) DAY,
       round(9 + (i % 50) * 1.7, 2)
FROM range(1, 1201) t(i);

-- A partner feed that sometimes omits the customer reference entirely.
INSERT INTO orders
SELECT 70000 + i, NULL,
       TIMESTAMP '2026-10-20 00:00:00' + INTERVAL (i % 10) DAY,
       round(12 + i * 0.9, 2)
FROM range(1, 26) t(i);

-- Orders from customers the CRM no longer has.
INSERT INTO orders
SELECT 100000 + i, 90000 + i,
       TIMESTAMP '2026-10-01 00:00:00' + INTERVAL (i % 20) DAY,
       round(10 + i * 0.5, 2)
FROM range(1, 51) t(i);
""")
counts = con.execute("""
    SELECT
      (SELECT count(*) FROM customers) AS customers,
      (SELECT count(*) FROM orders) AS orders,
      (SELECT count(*) FROM customers c WHERE NOT EXISTS (
         SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id
           AND o.ordered_at >= (SELECT max(ordered_at) FROM orders) - INTERVAL 90 DAY)) AS dormant,
      (SELECT count(DISTINCT strftime(ordered_at, '%Y-%m')) FROM orders) AS periods
""").fetchone()
con.close()
print(f"customers={counts[0]} orders={counts[1]} dormant={counts[2]} periods={counts[3]}")

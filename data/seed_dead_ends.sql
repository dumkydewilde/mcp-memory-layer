-- Dead-end tables: exist in the DB but NOT in the dbt manifest.
-- Used to test whether dbt context helps avoid non-managed tables.

-- 1. daily_revenue: partial/stale pre-aggregated revenue data
CREATE OR REPLACE TABLE daily_revenue AS
SELECT
    ordered_at AS date,
    location_id,
    count(*) AS total_orders,
    sum(order_total) AS total_revenue,
    avg(order_total) AS avg_order_value
FROM orders
WHERE ordered_at >= (SELECT max(ordered_at) - INTERVAL '30 days' FROM orders)
  AND location_id IN (SELECT location_id FROM locations LIMIT 3)  -- only 3 of 6 locations
GROUP BY ordered_at, location_id;

-- 2. customer_segments: wrong segment labels, stale snapshot
CREATE OR REPLACE TABLE customer_segments AS
SELECT
    customer_id,
    CASE
        WHEN lifetime_spend >= 500 THEN 'vip'
        WHEN lifetime_spend >= 100 THEN 'regular'
        ELSE 'at_risk'
    END AS segment,
    lifetime_spend AS lifetime_value,
    round(random(), 2) AS risk_score,
    DATE '2024-01-15' AS last_updated  -- visibly stale
FROM customers
WHERE customer_id IN (SELECT customer_id FROM customers LIMIT 500);  -- only half

-- 3. order_facts: denormalized but ambiguous and stale
CREATE OR REPLACE TABLE order_facts AS
SELECT
    o.order_id,
    c.customer_name,
    o.subtotal AS total,  -- ambiguous: is this pre-tax or post-tax?
    o.count_order_items AS item_count,
    o.ordered_at AS order_date
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
WHERE o.ordered_at < (SELECT max(ordered_at) - INTERVAL '7 days' FROM orders);  -- missing last week

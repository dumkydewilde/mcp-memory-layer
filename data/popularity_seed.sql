-- Pre-seeded popularity data simulating realistic usage patterns for jaffle_shop v3

INSERT INTO table_popularity VALUES
    ('customers', 150, '2025-02-01'),
    ('orders', 200, '2025-02-01'),
    ('order_items', 120, '2025-02-01'),
    ('products', 60, '2025-02-01'),
    ('stg_orders', 40, '2025-02-01'),
    ('stg_customers', 30, '2025-02-01'),
    ('stg_order_items', 25, '2025-02-01'),
    ('locations', 20, '2025-02-01'),
    ('supplies', 15, '2025-02-01'),
    ('raw_orders', 8, '2025-01-15'),
    ('raw_customers', 5, '2025-01-15'),
    ('raw_items', 3, '2025-01-10');

INSERT INTO join_patterns VALUES
    ('orders', 'customers', 'customer_id', 'LEFT', 120, '2025-02-01'),
    ('order_items', 'orders', 'order_id', 'LEFT', 95, '2025-02-01'),
    ('order_items', 'products', 'product_id', 'LEFT', 80, '2025-02-01'),
    ('order_items', 'supplies', 'product_id', 'LEFT', 30, '2025-02-01'),
    ('orders', 'locations', 'location_id', 'LEFT', 25, '2025-02-01');

INSERT INTO column_usage VALUES
    ('orders', 'ordered_at', 'group_by', 80),
    ('orders', 'order_total', 'select', 150),
    ('orders', 'customer_id', 'where', 90),
    ('orders', 'is_food_order', 'where', 40),
    ('customers', 'lifetime_spend', 'select', 70),
    ('customers', 'customer_id', 'select', 60),
    ('customers', 'customer_type', 'where', 55),
    ('customers', 'customer_name', 'select', 45),
    ('order_items', 'product_price', 'select', 50),
    ('order_items', 'supply_cost', 'select', 35),
    ('products', 'product_name', 'select', 40),
    ('orders', 'subtotal', 'select', 65),
    ('customers', 'count_lifetime_orders', 'select', 40)

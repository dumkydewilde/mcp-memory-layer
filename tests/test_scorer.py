"""Tests for the evaluation scorer."""

import sys
from pathlib import Path

import pytest

# eval is a builtin, so we need to add the project root to get the eval package
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.scorer import (
    contains_trap_pattern,
    extract_columns,
    extract_tables,
    has_correct_joins,
    score_response,
    score_text_response,
)


class TestExtractTables:
    def test_simple_select(self):
        tables = extract_tables("SELECT * FROM orders")
        assert "orders" in tables

    def test_join(self):
        tables = extract_tables(
            "SELECT * FROM orders JOIN customers ON orders.customer_id = customers.customer_id"
        )
        assert "orders" in tables
        assert "customers" in tables

    def test_subquery(self):
        tables = extract_tables(
            "SELECT * FROM (SELECT * FROM orders) sub JOIN customers ON sub.id = customers.id"
        )
        assert "orders" in tables
        assert "customers" in tables

    def test_cte(self):
        tables = extract_tables(
            "WITH o AS (SELECT * FROM orders) SELECT * FROM o"
        )
        assert "orders" in tables


class TestExtractColumns:
    def test_simple_select(self):
        cols = extract_columns("SELECT order_id, customer_id FROM orders")
        assert "order_id" in cols
        assert "customer_id" in cols

    def test_where_clause(self):
        cols = extract_columns("SELECT * FROM orders WHERE status = 'completed'")
        assert "status" in cols

    def test_aggregate(self):
        cols = extract_columns("SELECT SUM(order_total) FROM orders")
        assert "order_total" in cols


class TestContainsTrapPattern:
    def test_no_trap(self):
        assert not contains_trap_pattern("SELECT * FROM orders", None)

    def test_empty_sql(self):
        assert not contains_trap_pattern("", "some trap")

    def test_cents_trap_triggered(self):
        # Using raw_orders without dividing by 100
        assert contains_trap_pattern(
            "SELECT SUM(order_total) FROM raw_orders",
            "raw_orders.order_total is in cents, not dollars",
        )

    def test_cents_trap_avoided_with_division(self):
        assert not contains_trap_pattern(
            "SELECT SUM(order_total / 100) FROM raw_orders",
            "raw_orders.order_total is in cents, not dollars",
        )

    def test_cents_trap_avoided_with_multiply(self):
        assert not contains_trap_pattern(
            "SELECT SUM(order_total * 0.01) FROM raw_orders",
            "raw_orders.order_total is in cents, not dollars",
        )

    def test_sku_trap_triggered(self):
        assert contains_trap_pattern(
            "SELECT product_id FROM raw_items",
            "Using product_id on raw_items where the column is actually called sku",
        )

    def test_sku_trap_avoided(self):
        assert not contains_trap_pattern(
            "SELECT sku FROM raw_items",
            "Using product_id on raw_items where the column is actually called sku",
        )

    def test_wrong_value_trap(self):
        assert contains_trap_pattern(
            "SELECT * FROM customers WHERE customer_type = 'repeat'",
            "Using wrong values like 'repeat', 'existing'",
        )

    def test_correct_value_no_trap(self):
        assert not contains_trap_pattern(
            "SELECT * FROM customers WHERE customer_type = 'returning'",
            "Using wrong values like 'repeat', 'existing'",
        )

    def test_precomputed_trap_triggered(self):
        assert contains_trap_pattern(
            "SELECT c.customer_id, SUM(o.order_total) FROM customers c JOIN orders o ON c.customer_id = o.customer_id GROUP BY 1",
            "Re-aggregating from orders when customers already has lifetime_spend",
        )

    def test_precomputed_trap_avoided(self):
        assert not contains_trap_pattern(
            "SELECT customer_id, lifetime_spend FROM customers",
            "Re-aggregating from orders when customers already has lifetime_spend",
        )


class TestHasCorrectJoins:
    def test_empty_expected(self):
        assert has_correct_joins("SELECT 1", [])

    def test_correct_join(self):
        assert has_correct_joins(
            "SELECT * FROM orders JOIN customers ON customer_id = customer_id",
            ["customer_id"],
        )

    def test_missing_join_key(self):
        assert not has_correct_joins(
            "SELECT * FROM orders JOIN customers ON order_id = order_id",
            ["customer_id"],
        )

    def test_multiple_keys(self):
        assert has_correct_joins(
            "SELECT * FROM a JOIN b ON customer_id = x JOIN c ON order_id = y",
            ["customer_id", "order_id"],
        )


class TestScoreResponse:
    @pytest.fixture
    def expected(self):
        return {
            "tables": ["orders"],
            "columns": ["order_total"],
            "filters": [],
            "join_keys": [],
        }

    @pytest.fixture
    def scoring(self):
        return {
            "correct_table": 2,
            "correct_column": 1,
            "correct_filter": 0,
            "correct_join": 0,
            "avoids_trap": 3,
        }

    def test_none_sql_returns_zeros(self, expected, scoring):
        result = score_response(None, expected, scoring)
        assert all(v == 0 for v in result.values())

    def test_perfect_score(self, expected, scoring):
        sql = "SELECT SUM(order_total) FROM orders"
        result = score_response(sql, expected, scoring)
        assert result["correct_table"] == 2
        assert result["correct_column"] == 1
        assert result["avoids_trap"] == 3

    def test_wrong_table(self, expected, scoring):
        sql = "SELECT SUM(amount) FROM raw_payments"
        result = score_response(sql, expected, scoring)
        assert result["correct_table"] == 0

    def test_wrong_column(self, expected, scoring):
        sql = "SELECT COUNT(*) FROM orders"
        result = score_response(sql, expected, scoring)
        assert result["correct_table"] == 2
        assert result["correct_column"] == 0

    def test_filter_scoring(self):
        expected = {
            "tables": ["orders"],
            "columns": ["status"],
            "filters": ["status = 'completed'"],
            "join_keys": [],
        }
        scoring = {
            "correct_table": 2,
            "correct_column": 1,
            "correct_filter": 1,
            "correct_join": 0,
            "avoids_trap": 3,
        }
        sql = "SELECT * FROM orders WHERE status = 'completed'"
        result = score_response(sql, expected, scoring)
        assert result["correct_filter"] == 1

    def test_missing_filter(self):
        expected = {
            "tables": ["orders"],
            "columns": ["status"],
            "filters": ["status = 'completed'"],
            "join_keys": [],
        }
        scoring = {
            "correct_table": 2,
            "correct_column": 1,
            "correct_filter": 1,
            "correct_join": 0,
            "avoids_trap": 3,
        }
        sql = "SELECT * FROM orders"
        result = score_response(sql, expected, scoring)
        assert result["correct_filter"] == 0

    def test_join_scoring(self):
        expected = {
            "tables": ["orders", "customers"],
            "columns": ["order_total"],
            "filters": [],
            "join_keys": ["customer_id"],
        }
        scoring = {
            "correct_table": 2,
            "correct_column": 1,
            "correct_filter": 0,
            "correct_join": 2,
            "avoids_trap": 3,
        }
        sql = "SELECT order_total FROM orders JOIN customers ON customer_id = customer_id"
        result = score_response(sql, expected, scoring)
        assert result["correct_join"] == 2

    def test_trap_in_expected(self):
        expected = {
            "tables": ["orders"],
            "columns": ["order_total"],
            "filters": [],
            "join_keys": [],
            "trap": "raw_orders.order_total is in cents, not dollars",
        }
        scoring = {
            "correct_table": 2,
            "correct_column": 1,
            "correct_filter": 0,
            "correct_join": 0,
            "avoids_trap": 3,
        }
        # Good SQL: uses orders mart
        result = score_response("SELECT order_total FROM orders", expected, scoring)
        assert result["avoids_trap"] == 3

        # Bad SQL: uses raw_orders without conversion
        result = score_response("SELECT order_total FROM raw_orders", expected, scoring)
        assert result["avoids_trap"] == 0


class TestScoreTextResponse:
    def test_none_text_returns_zeros(self):
        expected = {"keywords": ["customer_id"], "keyword_threshold": 1}
        scoring = {"correct_keywords": 3, "avoids_trap": 2}
        result = score_text_response(None, expected, scoring)
        assert result == {"correct_keywords": 0, "avoids_trap": 0}

    def test_keywords_above_threshold(self):
        expected = {
            "keywords": ["customer_id", "customer_name", "lifetime_spend"],
            "keyword_threshold": 2,
        }
        scoring = {"correct_keywords": 4, "avoids_trap": 0}
        text = "The customers model has customer_id, customer_name, and more."
        result = score_text_response(text, expected, scoring)
        assert result["correct_keywords"] == 4

    def test_keywords_below_threshold(self):
        expected = {
            "keywords": ["customer_id", "customer_name", "lifetime_spend"],
            "keyword_threshold": 3,
        }
        scoring = {"correct_keywords": 4, "avoids_trap": 0}
        text = "The model has a customer_id column."
        result = score_text_response(text, expected, scoring)
        assert result["correct_keywords"] == 0

    def test_anti_keywords_present(self):
        expected = {
            "keywords": ["new", "returning"],
            "keyword_threshold": 2,
            "anti_keywords": ["active", "inactive"],
        }
        scoring = {"correct_keywords": 2, "avoids_trap": 3}
        text = "The values are new, returning, and active."
        result = score_text_response(text, expected, scoring)
        assert result["correct_keywords"] == 2  # keywords pass
        assert result["avoids_trap"] == 0  # anti-keyword "active" found

    def test_anti_keywords_absent(self):
        expected = {
            "keywords": ["new", "returning"],
            "keyword_threshold": 2,
            "anti_keywords": ["active", "inactive"],
        }
        scoring = {"correct_keywords": 2, "avoids_trap": 3}
        text = "The accepted values are 'new' and 'returning'."
        result = score_text_response(text, expected, scoring)
        assert result["correct_keywords"] == 2
        assert result["avoids_trap"] == 3

    def test_no_keywords_defined(self):
        expected = {"keyword_threshold": 1}
        scoring = {"correct_keywords": 3, "avoids_trap": 0}
        result = score_text_response("some text", expected, scoring)
        assert result["correct_keywords"] == 3  # defaults to full score

    def test_case_insensitive(self):
        expected = {
            "keywords": ["Customer_ID", "LIFETIME_SPEND"],
            "keyword_threshold": 2,
        }
        scoring = {"correct_keywords": 4, "avoids_trap": 0}
        text = "customer_id and lifetime_spend are key columns."
        result = score_text_response(text, expected, scoring)
        assert result["correct_keywords"] == 4

"""Tests for the popularity tracker."""

from pathlib import Path

import pytest

from mcp_memory.popularity import PopularityTracker


@pytest.fixture
def tracker(tmp_path: Path) -> PopularityTracker:
    pt = PopularityTracker(tmp_path / "test_popularity.duckdb")
    yield pt
    pt.close()


class TestTableCreation:
    def test_tables_exist(self, tracker: PopularityTracker):
        tables = tracker.db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchall()
        names = {t[0] for t in tables}
        assert "table_popularity" in names
        assert "join_patterns" in names
        assert "column_usage" in names


class TestRecordQuery:
    def test_records_table_popularity(self, tracker: PopularityTracker):
        tracker.record_query("SELECT * FROM orders")
        row = tracker.db.execute(
            "SELECT query_count FROM table_popularity WHERE table_name = 'orders'"
        ).fetchone()
        assert row is not None
        assert row[0] == 1

    def test_increments_on_repeat(self, tracker: PopularityTracker):
        tracker.record_query("SELECT * FROM orders")
        tracker.record_query("SELECT order_id FROM orders WHERE status = 'completed'")
        row = tracker.db.execute(
            "SELECT query_count FROM table_popularity WHERE table_name = 'orders'"
        ).fetchone()
        assert row[0] == 2

    def test_records_multiple_tables(self, tracker: PopularityTracker):
        tracker.record_query(
            "SELECT * FROM orders JOIN customers ON orders.customer_id = customers.customer_id"
        )
        orders = tracker.db.execute(
            "SELECT query_count FROM table_popularity WHERE table_name = 'orders'"
        ).fetchone()
        customers = tracker.db.execute(
            "SELECT query_count FROM table_popularity WHERE table_name = 'customers'"
        ).fetchone()
        assert orders[0] == 1
        assert customers[0] == 1

    def test_records_join_pattern(self, tracker: PopularityTracker):
        tracker.record_query(
            "SELECT * FROM orders JOIN customers ON orders.customer_id = customers.customer_id"
        )
        join = tracker.db.execute(
            "SELECT left_table, right_table, join_key FROM join_patterns"
        ).fetchone()
        assert join is not None
        assert join[1] == "customers"
        assert join[2] == "customer_id"

    def test_records_column_usage(self, tracker: PopularityTracker):
        tracker.record_query("SELECT order_total FROM orders WHERE status = 'completed'")
        cols = tracker.db.execute(
            "SELECT column_name, usage_context FROM column_usage ORDER BY column_name"
        ).fetchall()
        col_dict = {c[0]: c[1] for c in cols}
        assert "order_total" in col_dict
        assert "status" in col_dict

    def test_skips_non_select(self, tracker: PopularityTracker):
        tracker.record_query("CREATE TABLE foo (id INT)")
        count = tracker.db.execute("SELECT count(*) FROM table_popularity").fetchone()[0]
        assert count == 0

    def test_skips_unparseable(self, tracker: PopularityTracker):
        tracker.record_query("THIS IS NOT SQL AT ALL ???")
        count = tracker.db.execute("SELECT count(*) FROM table_popularity").fetchone()[0]
        assert count == 0

    def test_column_context_where(self, tracker: PopularityTracker):
        tracker.record_query("SELECT * FROM orders WHERE status = 'completed'")
        row = tracker.db.execute(
            "SELECT usage_context FROM column_usage WHERE column_name = 'status'"
        ).fetchone()
        assert row is not None
        assert row[0] == "where"

    def test_column_context_group_by(self, tracker: PopularityTracker):
        tracker.record_query("SELECT status, count(*) FROM orders GROUP BY status")
        row = tracker.db.execute(
            "SELECT usage_context FROM column_usage "
            "WHERE column_name = 'status' AND usage_context = 'group_by'"
        ).fetchone()
        assert row is not None

    def test_column_context_order_by(self, tracker: PopularityTracker):
        tracker.record_query("SELECT * FROM orders ORDER BY ordered_at")
        row = tracker.db.execute(
            "SELECT usage_context FROM column_usage "
            "WHERE column_name = 'ordered_at' AND usage_context = 'order_by'"
        ).fetchone()
        assert row is not None


class TestGetPopularContext:
    def test_empty_returns_message(self, tracker: PopularityTracker):
        result = tracker.get_popular_context()
        assert "Most queried" in result

    def test_overall_shows_top_tables(self, tracker: PopularityTracker):
        tracker.record_query("SELECT * FROM orders")
        tracker.record_query("SELECT * FROM orders")
        tracker.record_query("SELECT * FROM customers")
        result = tracker.get_popular_context()
        assert "orders: 2 queries" in result
        assert "customers: 1 queries" in result

    def test_filtered_by_table(self, tracker: PopularityTracker):
        tracker.record_query("SELECT * FROM orders")
        result = tracker.get_popular_context(tables=["orders"])
        assert "**orders**" in result
        assert "queried 1 times" in result

    def test_filtered_unknown_table(self, tracker: PopularityTracker):
        result = tracker.get_popular_context(tables=["nonexistent"])
        # Should return empty-ish since no data matches
        assert result == "" or "nonexistent" not in result

    def test_shows_join_patterns(self, tracker: PopularityTracker):
        tracker.record_query(
            "SELECT * FROM orders JOIN customers ON orders.customer_id = customers.customer_id"
        )
        result = tracker.get_popular_context()
        assert "Common join patterns" in result
        assert "customers" in result


class TestSeed:
    def test_seed_loads_data(self, tracker: PopularityTracker, tmp_path: Path):
        seed_file = tmp_path / "seed.sql"
        seed_file.write_text(
            "INSERT INTO table_popularity VALUES ('orders', 100, '2025-01-01');\n"
            "INSERT INTO table_popularity VALUES ('customers', 50, '2025-01-01');"
        )
        tracker.seed(seed_file)
        row = tracker.db.execute(
            "SELECT query_count FROM table_popularity WHERE table_name = 'orders'"
        ).fetchone()
        assert row[0] == 100

    def test_seed_nonexistent_file(self, tracker: PopularityTracker, tmp_path: Path):
        # Should not raise
        tracker.seed(tmp_path / "nope.sql")

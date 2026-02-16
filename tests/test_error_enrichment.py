"""Tests for query error enrichment with dbt context."""

import json
from pathlib import Path

import pytest

from mcp_memory.dbt_context import DbtManifest, extract_tables_from_sql

SAMPLE_MANIFEST = {
    "nodes": {
        "model.test.events": {
            "resource_type": "model",
            "name": "events",
            "description": "Organization events",
            "config": {"materialized": "table"},
            "columns": {
                "ts": {"description": "Timestamp when the event occurred"},
                "event_category": {"description": "Category of event"},
                "event_name": {"description": "Name of event"},
                "organization_id": {"description": "Org ID"},
            },
            "depends_on": {"nodes": []},
            "raw_code": "SELECT * FROM raw_events",
        },
        "model.test.orders": {
            "resource_type": "model",
            "name": "orders",
            "description": "Orders table",
            "config": {"materialized": "table"},
            "columns": {
                "order_id": {"description": "Primary key"},
                "customer_id": {"description": "FK to customers"},
                "order_date": {"description": "Date of order"},
            },
            "depends_on": {"nodes": []},
            "raw_code": "SELECT * FROM raw_orders",
        },
    }
}


@pytest.fixture
def manifest(tmp_path: Path) -> DbtManifest:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(SAMPLE_MANIFEST))
    return DbtManifest(path)


class TestExtractTablesFromSql:
    def test_simple_select(self):
        tables = extract_tables_from_sql("SELECT * FROM events WHERE ts > '2026-01-01'")
        assert "events" in tables

    def test_qualified_table_name(self):
        tables = extract_tables_from_sql("SELECT * FROM mdw.gtm.events")
        assert "events" in tables

    def test_join(self):
        tables = extract_tables_from_sql(
            "SELECT * FROM events e JOIN orders o ON e.organization_id = o.org_id"
        )
        assert "events" in tables
        assert "orders" in tables

    def test_subquery(self):
        tables = extract_tables_from_sql(
            "SELECT * FROM (SELECT * FROM events) sub JOIN orders ON true"
        )
        assert "events" in tables
        assert "orders" in tables

    def test_invalid_sql_falls_back_to_regex(self):
        tables = extract_tables_from_sql("SELECT FROM events WHERE ??? BROKEN")
        assert "events" in tables

    def test_cte(self):
        tables = extract_tables_from_sql(
            "WITH e AS (SELECT * FROM events) SELECT * FROM e JOIN orders ON true"
        )
        assert "events" in tables


class TestEnrichError:
    def test_column_not_found_shows_available_columns(self, manifest: DbtManifest):
        error = 'Referenced column "event_date" not found in FROM clause!'
        sql = "SELECT event_date FROM events"
        result = manifest.enrich_error(error, sql)

        assert "dbt context" in result
        assert "ts" in result
        assert "event_category" in result
        assert "event_name" in result
        assert "organization_id" in result

    def test_column_error_with_qualified_name(self, manifest: DbtManifest):
        error = 'Referenced column "event_date" not found in FROM clause!'
        sql = "SELECT event_date FROM mdw.gtm.events"
        result = manifest.enrich_error(error, sql)

        assert "ts" in result

    def test_column_error_with_join(self, manifest: DbtManifest):
        error = 'Referenced column "created_at" not found in FROM clause!'
        sql = (
            "SELECT * FROM events e JOIN orders o ON e.org_id = o.org_id"
            " WHERE created_at > '2026-01-01'"
        )
        result = manifest.enrich_error(error, sql)

        assert "events" in result
        assert "orders" in result
        assert "ts" in result
        assert "order_date" in result

    def test_table_not_found_suggests_similar(self, manifest: DbtManifest):
        error = "Table with name event does not exist!"
        sql = "SELECT * FROM event"
        result = manifest.enrich_error(error, sql)

        assert "not found" in result.lower()
        assert "events" in result

    def test_non_matching_error_passes_through(self, manifest: DbtManifest):
        error = "Syntax error at position 42"
        sql = "SELECT * FORM events"
        result = manifest.enrich_error(error, sql)

        assert result == error
        assert "dbt context" not in result

    def test_unknown_table_no_hint(self, manifest: DbtManifest):
        error = 'Referenced column "foo" not found in FROM clause!'
        sql = "SELECT foo FROM completely_unknown_table"
        result = manifest.enrich_error(error, sql)

        # No dbt model matches, so no enrichment
        assert "dbt context" not in result

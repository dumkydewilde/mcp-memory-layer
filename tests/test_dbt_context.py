"""Tests for the dbt manifest parser."""

import json
from pathlib import Path

import pytest

from mcp_memory.dbt_context import DbtManifest, ModelContext

SAMPLE_MANIFEST = {
    "nodes": {
        "model.jaffle_shop.customers": {
            "resource_type": "model",
            "name": "customers",
            "description": "Customer overview data mart.",
            "config": {"materialized": "table"},
            "columns": {
                "customer_id": {"description": "The unique customer key."},
                "customer_name": {"description": "Full name."},
                "lifetime_spend": {"description": "Total spend including tax."},
            },
            "depends_on": {"nodes": ["model.jaffle_shop.stg_customers", "model.jaffle_shop.orders"]},
            "raw_code": "SELECT * FROM {{ ref('stg_customers') }}",
        },
        "model.jaffle_shop.stg_orders": {
            "resource_type": "model",
            "name": "stg_orders",
            "description": "Cleaned orders with cents converted to dollars.",
            "config": {"materialized": "view"},
            "columns": {
                "order_id": {"description": "Primary key."},
                "customer_id": {"description": "FK to customers."},
            },
            "depends_on": {"nodes": ["seed.jaffle_shop.raw_orders"]},
            "raw_code": "SELECT id AS order_id FROM raw_orders",
        },
        "seed.jaffle_shop.raw_orders": {
            "resource_type": "seed",
            "name": "raw_orders",
            "description": "",
            "config": {"materialized": "seed"},
            "columns": {},
            "depends_on": {"nodes": []},
            "raw_code": "",
        },
        "test.jaffle_shop.not_null_customers_customer_id": {
            "resource_type": "test",
            "name": "not_null_customers_customer_id",
            "test_metadata": {"name": "not_null"},
            "depends_on": {"nodes": ["model.jaffle_shop.customers"]},
        },
        "test.jaffle_shop.unique_stg_orders_order_id": {
            "resource_type": "test",
            "name": "unique_stg_orders_order_id",
            "test_metadata": {"name": "unique"},
            "depends_on": {"nodes": ["model.jaffle_shop.stg_orders"]},
        },
    }
}


@pytest.fixture
def manifest_path(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(SAMPLE_MANIFEST))
    return path


@pytest.fixture
def manifest(manifest_path: Path) -> DbtManifest:
    return DbtManifest(manifest_path)


class TestManifestParsing:
    def test_loads_models_and_seeds(self, manifest: DbtManifest):
        assert "customers" in manifest.models
        assert "stg_orders" in manifest.models
        assert "raw_orders" in manifest.models

    def test_skips_tests(self, manifest: DbtManifest):
        assert "not_null_customers_customer_id" not in manifest.models

    def test_model_context_fields(self, manifest: DbtManifest):
        m = manifest.models["customers"]
        assert isinstance(m, ModelContext)
        assert m.name == "customers"
        assert m.materialized == "table"
        assert m.description == "Customer overview data mart."
        assert "customer_id" in m.columns
        assert m.columns["customer_id"] == "The unique customer key."

    def test_depends_on_extracts_model_names(self, manifest: DbtManifest):
        m = manifest.models["customers"]
        assert "stg_customers" in m.depends_on
        assert "orders" in m.depends_on

    def test_depends_on_includes_seeds(self, manifest: DbtManifest):
        m = manifest.models["stg_orders"]
        assert "raw_orders" in m.depends_on

    def test_tests_attached_to_models(self, manifest: DbtManifest):
        assert "not_null" in manifest.models["customers"].tests
        assert "unique" in manifest.models["stg_orders"].tests

    def test_raw_sql_truncation(self, tmp_path: Path):
        long_sql = "SELECT " + ", ".join(f"col_{i}" for i in range(200))
        manifest_data = {
            "nodes": {
                "model.test.big": {
                    "resource_type": "model",
                    "name": "big",
                    "description": "",
                    "config": {"materialized": "table"},
                    "columns": {},
                    "depends_on": {"nodes": []},
                    "raw_code": long_sql,
                }
            }
        }
        path = tmp_path / "big_manifest.json"
        path.write_text(json.dumps(manifest_data))
        dm = DbtManifest(path)
        assert dm.models["big"].raw_sql.endswith("-- [truncated]")
        assert len(dm.models["big"].raw_sql) < len(long_sql)

    def test_nonexistent_manifest(self, tmp_path: Path):
        dm = DbtManifest(tmp_path / "nope.json")
        assert dm.models == {}


class TestGetContext:
    def test_existing_model(self, manifest: DbtManifest):
        result = manifest.get_context("customers")
        assert "## customers (table)" in result
        assert "Customer overview data mart." in result
        assert "customer_id" in result
        assert "Upstream:" in result

    def test_includes_sql(self, manifest: DbtManifest):
        result = manifest.get_context("customers")
        assert "```sql" in result

    def test_includes_tests(self, manifest: DbtManifest):
        result = manifest.get_context("customers")
        assert "not_null" in result

    def test_missing_model_with_suggestion(self, manifest: DbtManifest):
        result = manifest.get_context("customer")  # close match
        assert "Did you mean" in result
        assert "customers" in result

    def test_missing_model_no_suggestion(self, manifest: DbtManifest):
        result = manifest.get_context("nonexistent_xyz")
        assert "not found" in result
        assert "Did you mean" not in result


class TestListModels:
    def test_returns_table(self, manifest: DbtManifest):
        result = manifest.list_models()
        assert "| Model |" in result
        assert "customers" in result
        assert "stg_orders" in result
        assert "raw_orders" in result

    def test_shows_count_header(self, manifest: DbtManifest):
        result = manifest.list_models()
        assert "Showing 3 of 3 models" in result

    def test_empty_manifest(self, tmp_path: Path):
        dm = DbtManifest(tmp_path / "nope.json")
        assert dm.list_models() == "No dbt models loaded."

    def test_search_by_name(self, manifest: DbtManifest):
        result = manifest.list_models(search="order")
        assert "stg_orders" in result
        assert "raw_orders" in result
        assert "customers" not in result
        assert "matching 'order'" in result

    def test_search_by_description(self, manifest: DbtManifest):
        result = manifest.list_models(search="cents")
        assert "stg_orders" in result
        assert "customers" not in result

    def test_search_by_column_name(self, manifest: DbtManifest):
        result = manifest.list_models(search="lifetime_spend")
        assert "customers" in result
        assert "stg_orders" not in result

    def test_search_case_insensitive(self, manifest: DbtManifest):
        result = manifest.list_models(search="CUSTOMER")
        assert "customers" in result

    def test_search_multi_term(self, manifest: DbtManifest):
        result = manifest.list_models(search="customer cents")
        assert "customers" in result  # matches "customer" in name
        assert "stg_orders" in result  # matches "cents" in description

    def test_search_no_match(self, manifest: DbtManifest):
        result = manifest.list_models(search="nonexistent_xyz")
        assert result == "No models matching 'nonexistent_xyz'."

    def test_description_truncation(self, tmp_path: Path):
        manifest_data = {
            "nodes": {
                "model.test.verbose": {
                    "resource_type": "model",
                    "name": "verbose",
                    "description": "A" * 100,
                    "config": {"materialized": "table"},
                    "columns": {},
                    "depends_on": {"nodes": []},
                    "raw_code": "",
                }
            }
        }
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(manifest_data))
        dm = DbtManifest(path)
        result = dm.list_models()
        assert "..." in result

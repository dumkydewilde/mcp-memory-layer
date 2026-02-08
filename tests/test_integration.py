"""Integration tests using the real jaffle_shop data."""

from pathlib import Path

import pytest

from mcp_memory.corrections import CorrectionsStore
from mcp_memory.dbt_context import DbtManifest
from mcp_memory.popularity import PopularityTracker

DATA_DIR = Path(__file__).parent.parent / "data"
JAFFLE_MANIFEST = DATA_DIR / "jaffle_shop" / "target" / "manifest.json"
CORRECTIONS_JSON = DATA_DIR / "corrections.json"
POPULARITY_SEED = DATA_DIR / "popularity_seed.sql"

needs_jaffle = pytest.mark.skipif(
    not JAFFLE_MANIFEST.exists(),
    reason="jaffle_shop not built (run dbt build first)",
)


@needs_jaffle
class TestRealManifest:
    @pytest.fixture
    def manifest(self) -> DbtManifest:
        return DbtManifest(JAFFLE_MANIFEST)

    def test_loads_all_expected_models(self, manifest: DbtManifest):
        expected = {
            "customers", "orders", "order_items", "products", "locations", "supplies",
            "stg_customers", "stg_orders", "stg_order_items", "stg_products",
            "stg_locations", "stg_supplies",
        }
        assert expected.issubset(set(manifest.models.keys()))

    def test_customers_has_columns(self, manifest: DbtManifest):
        m = manifest.models["customers"]
        assert "customer_id" in m.columns
        assert "customer_name" in m.columns
        assert "lifetime_spend" in m.columns

    def test_orders_has_description(self, manifest: DbtManifest):
        m = manifest.models["orders"]
        assert m.description != ""
        assert "order" in m.description.lower()

    def test_staging_models_are_views(self, manifest: DbtManifest):
        for name in ["stg_customers", "stg_orders", "stg_order_items"]:
            assert manifest.models[name].materialized == "view"

    def test_mart_models_are_tables(self, manifest: DbtManifest):
        for name in ["customers", "orders", "order_items"]:
            assert manifest.models[name].materialized == "table"

    def test_orders_has_tests(self, manifest: DbtManifest):
        assert len(manifest.models["orders"].tests) > 0

    def test_customers_context_output(self, manifest: DbtManifest):
        ctx = manifest.get_context("customers")
        assert "## customers (table)" in ctx
        assert "customer_id" in ctx
        assert "lifetime_spend" in ctx


class TestRealCorrections:
    @pytest.fixture
    def store(self) -> CorrectionsStore:
        return CorrectionsStore(CORRECTIONS_JSON)

    def test_loads_10_corrections(self, store: CorrectionsStore):
        assert len(store.corrections) == 10

    def test_cents_trap_matches_revenue_question(self, store: CorrectionsStore):
        result = store.get_corrections("What is the total revenue?")
        assert "CORRECTIONS" in result
        assert "cents" in result.lower() or "dollars" in result.lower()

    def test_customer_column_matches_join_question(self, store: CorrectionsStore):
        result = store.get_corrections(
            "Join raw_orders to customers", tables=["raw_orders"]
        )
        assert "customer" in result.lower()

    def test_lifetime_spend_matches(self, store: CorrectionsStore):
        result = store.get_corrections("Who is the most valuable customer?")
        assert "lifetime" in result.lower() or "pre-computed" in result.lower()

    def test_food_drink_matches(self, store: CorrectionsStore):
        result = store.get_corrections("Which orders have food items?")
        assert "food" in result.lower()


class TestRealPopularity:
    @pytest.fixture
    def tracker(self, tmp_path: Path) -> PopularityTracker:
        pt = PopularityTracker(tmp_path / "pop.duckdb")
        pt.seed(POPULARITY_SEED)
        yield pt
        pt.close()

    def test_seed_loaded(self, tracker: PopularityTracker):
        count = tracker.db.execute("SELECT count(*) FROM table_popularity").fetchone()[0]
        assert count >= 10

    def test_orders_most_popular(self, tracker: PopularityTracker):
        top = tracker.db.execute(
            "SELECT table_name FROM table_popularity ORDER BY query_count DESC LIMIT 1"
        ).fetchone()
        assert top[0] == "orders"

    def test_join_patterns_loaded(self, tracker: PopularityTracker):
        count = tracker.db.execute("SELECT count(*) FROM join_patterns").fetchone()[0]
        assert count >= 3

    def test_context_output(self, tracker: PopularityTracker):
        result = tracker.get_popular_context()
        assert "orders" in result
        assert "customers" in result

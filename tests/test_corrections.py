"""Tests for the corrections store."""

import json
from pathlib import Path

import pytest

from mcp_memory.corrections import CorrectionsStore


@pytest.fixture
def corrections_path(tmp_path: Path) -> Path:
    return tmp_path / "corrections.json"


@pytest.fixture
def seeded_store(corrections_path: Path) -> CorrectionsStore:
    """Store pre-loaded with a few test corrections."""
    data = {
        "version": 1,
        "corrections": [
            {
                "id": "corr_001",
                "scope": {
                    "tables": ["raw_orders"],
                    "columns": ["subtotal", "order_total"],
                    "keywords": ["revenue", "total", "cents"],
                },
                "correction": "raw_orders amounts are in cents, divide by 100.",
                "category": "unit_conversion",
                "source": "manual",
            },
            {
                "id": "corr_002",
                "scope": {
                    "tables": ["raw_orders"],
                    "columns": ["customer"],
                    "keywords": ["customer", "join"],
                },
                "correction": "raw_orders uses 'customer' not 'customer_id'.",
                "category": "naming_convention",
                "source": "manual",
            },
            {
                "id": "corr_003",
                "scope": {
                    "tables": ["customers"],
                    "columns": ["lifetime_spend"],
                    "keywords": ["lifetime", "total spend"],
                },
                "correction": "customers.lifetime_spend is pre-computed.",
                "category": "precomputed_metric",
                "source": "manual",
            },
        ],
    }
    corrections_path.write_text(json.dumps(data))
    return CorrectionsStore(corrections_path)


class TestCorrectionsStoreLoading:
    def test_load_from_nonexistent_file(self, corrections_path: Path):
        store = CorrectionsStore(corrections_path)
        assert store.corrections == []

    def test_load_from_existing_file(self, seeded_store: CorrectionsStore):
        assert len(seeded_store.corrections) == 3

    def test_load_preserves_correction_fields(self, seeded_store: CorrectionsStore):
        first = seeded_store.corrections[0]
        assert first["id"] == "corr_001"
        assert first["scope"]["tables"] == ["raw_orders"]
        assert "cents" in first["correction"]


class TestGetCorrections:
    def test_no_match_returns_message(self, seeded_store: CorrectionsStore):
        result = seeded_store.get_corrections("What color is the sky?")
        assert result == "No relevant corrections found."

    def test_keyword_match(self, seeded_store: CorrectionsStore):
        result = seeded_store.get_corrections("What is the total revenue?")
        assert "CORRECTIONS" in result
        assert "cents" in result

    def test_table_filter_boosts_score(self, seeded_store: CorrectionsStore):
        result = seeded_store.get_corrections("Show data", tables=["raw_orders"])
        assert "raw_orders" in result

    def test_column_mention_match(self, seeded_store: CorrectionsStore):
        result = seeded_store.get_corrections("What is the lifetime_spend for each customer?")
        assert "pre-computed" in result

    def test_returns_at_most_3(self, seeded_store: CorrectionsStore):
        # "customer" matches corr_002 keyword, "total" matches corr_001
        result = seeded_store.get_corrections("customer total revenue join")
        # Count correction bullets
        bullets = [line for line in result.split("\n") if line.startswith("- ")]
        assert len(bullets) <= 3

    def test_higher_score_ranked_first(self, seeded_store: CorrectionsStore):
        # "cents" + "total" + "revenue" should strongly match corr_001
        result = seeded_store.get_corrections("total revenue in cents")
        lines = result.split("\n")
        # First correction bullet should be about cents
        first_bullet = next(l for l in lines if l.startswith("- "))
        assert "cents" in first_bullet


class TestSaveCorrection:
    def test_save_adds_to_store(self, corrections_path: Path):
        store = CorrectionsStore(corrections_path)
        assert len(store.corrections) == 0

        result = store.save_correction(
            correction="Test correction",
            tables=["test_table"],
            columns=["col1"],
            keywords=["test"],
        )
        assert "corr_001" in result
        assert len(store.corrections) == 1

    def test_save_persists_to_disk(self, corrections_path: Path):
        store = CorrectionsStore(corrections_path)
        store.save_correction("Persisted", tables=["t1"])

        # Reload from disk
        store2 = CorrectionsStore(corrections_path)
        assert len(store2.corrections) == 1
        assert store2.corrections[0]["correction"] == "Persisted"

    def test_save_increments_id(self, seeded_store: CorrectionsStore):
        result = seeded_store.save_correction("New one", tables=["t"])
        assert "corr_004" in result  # 3 existing + 1 new

    def test_saved_correction_is_findable(self, corrections_path: Path):
        store = CorrectionsStore(corrections_path)
        store.save_correction(
            "Always use UTC timestamps",
            tables=["events"],
            keywords=["timestamp", "timezone"],
        )
        result = store.get_corrections("What timezone are timestamps in?")
        assert "UTC" in result

    def test_save_defaults_optional_fields(self, corrections_path: Path):
        store = CorrectionsStore(corrections_path)
        store.save_correction("Minimal", tables=["t"])
        corr = store.corrections[0]
        assert corr["scope"]["columns"] == []
        assert corr["scope"]["keywords"] == []
        assert corr["source"] == "learned"

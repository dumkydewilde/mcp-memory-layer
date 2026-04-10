"""Tests for config loading and CLI init command."""

import os
from pathlib import Path

import pytest

from mcp_memory.config import load_config


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a temporary config directory."""
    return tmp_path / ".mcp-memory"


@pytest.fixture
def clean_env(monkeypatch):
    """Remove all MCP_MEMORY_ env vars for a clean test."""
    for key in list(os.environ):
        if key.startswith("MCP_MEMORY_"):
            monkeypatch.delenv(key, raising=False)


def test_default_config(clean_env, tmp_path, monkeypatch):
    """Config with no toml and no env vars returns defaults."""
    monkeypatch.setenv("MCP_MEMORY_CONFIG", str(tmp_path / "nonexistent.toml"))
    cfg = load_config()
    assert cfg.enable_query is True
    assert cfg.enable_corrections is True
    assert cfg.enable_dbt is True
    assert cfg.enable_popularity is True


def test_config_from_toml(clean_env, tmp_config_dir):
    """Config loads paths and features from TOML."""
    tmp_config_dir.mkdir(parents=True)
    config_file = tmp_config_dir / "config.toml"
    config_file.write_text("""
[paths]
duckdb = "/my/db.duckdb"
manifest = "/my/manifest.json"
corrections = "/my/corrections.json"

[features]
popularity = false
""")

    cfg = load_config(config_file)
    assert cfg.duckdb_path == Path("/my/db.duckdb")
    assert cfg.manifest_source == "/my/manifest.json"
    assert cfg.manifest_path == Path("/my/manifest.json")  # backward-compat property
    assert cfg.corrections_path == Path("/my/corrections.json")
    assert cfg.enable_popularity is False
    assert cfg.enable_corrections is True  # not overridden


def test_manifest_source_url(clean_env, tmp_config_dir):
    """Config preserves URL manifest sources as strings."""
    tmp_config_dir.mkdir(parents=True)
    config_file = tmp_config_dir / "config.toml"
    config_file.write_text("""
[paths]
manifest = "https://my-bucket.s3.amazonaws.com/manifest.json"
""")
    cfg = load_config(config_file)
    assert cfg.manifest_source == "https://my-bucket.s3.amazonaws.com/manifest.json"


def test_env_vars_override_toml(clean_env, tmp_config_dir, monkeypatch):
    """Env vars take precedence over TOML values."""
    tmp_config_dir.mkdir(parents=True)
    config_file = tmp_config_dir / "config.toml"
    config_file.write_text("""
[paths]
duckdb = "/toml/db.duckdb"
""")

    monkeypatch.setenv("MCP_MEMORY_DUCKDB_PATH", "/env/db.duckdb")
    cfg = load_config(config_file)
    assert cfg.duckdb_path == Path("/env/db.duckdb")


def test_data_dir_env_sets_defaults(clean_env, tmp_path, monkeypatch):
    """MCP_MEMORY_DATA_DIR sets default paths for duckdb, corrections, popularity."""
    monkeypatch.setenv("MCP_MEMORY_DATA_DIR", str(tmp_path / "mydata"))
    monkeypatch.setenv("MCP_MEMORY_CONFIG", str(tmp_path / "nonexistent.toml"))
    cfg = load_config()
    assert cfg.corrections_path == tmp_path / "mydata" / "corrections.json"
    assert cfg.popularity_db_path == tmp_path / "mydata" / "popularity.duckdb"


def test_feature_flag_env_override(clean_env, tmp_path, monkeypatch):
    """Feature flags from env vars override defaults."""
    monkeypatch.setenv("MCP_MEMORY_CONFIG", str(tmp_path / "nonexistent.toml"))
    monkeypatch.setenv("MCP_MEMORY_DBT", "false")
    cfg = load_config()
    assert cfg.enable_dbt is False
    assert cfg.enable_corrections is True


def test_motherduck_config_from_toml(clean_env, tmp_config_dir):
    """MotherDuck remote MCP settings load from TOML."""
    tmp_config_dir.mkdir(parents=True)
    config_file = tmp_config_dir / "config.toml"
    config_file.write_text("""
[motherduck]
mcp_url = "https://api.motherduck.com/mcp"
token = "secret-token"

[features]
motherduck_query_rw = true
""")

    cfg = load_config(config_file)
    assert cfg.motherduck_mcp_url == "https://api.motherduck.com/mcp"
    assert cfg.motherduck_token == "secret-token"
    assert cfg.motherduck_headers["Authorization"] == "Bearer secret-token"
    assert cfg.enable_motherduck_query_rw is True


def test_motherduck_env_vars_override_toml(clean_env, tmp_config_dir, monkeypatch):
    """MotherDuck env vars override TOML values."""
    tmp_config_dir.mkdir(parents=True)
    config_file = tmp_config_dir / "config.toml"
    config_file.write_text("""
[motherduck]
mcp_url = "https://example.com/mcp"
token = "toml-token"
""")

    monkeypatch.setenv("MCP_MEMORY_MOTHERDUCK_MCP_URL", "https://api.motherduck.com/mcp")
    monkeypatch.setenv("MCP_MEMORY_MOTHERDUCK_AUTH_HEADER", "Bearer env-token")
    monkeypatch.setenv("MCP_MEMORY_MOTHERDUCK_QUERY_RW", "true")
    cfg = load_config(config_file)

    assert cfg.motherduck_mcp_url == "https://api.motherduck.com/mcp"
    assert cfg.motherduck_headers["Authorization"] == "Bearer env-token"
    assert cfg.enable_motherduck_query_rw is True

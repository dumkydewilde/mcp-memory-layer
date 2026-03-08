"""Configuration loader — reads from config.toml, env vars, or defaults."""

import os
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

DEFAULT_CONFIG_DIR = Path.home() / ".mcp-memory"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.toml"


@dataclass
class Config:
    """Resolved configuration for the MCP memory server."""

    duckdb_path: Path = Path("data/jaffle_shop/jaffle_shop.duckdb")
    manifest_source: str = "dbt_project/target/manifest.json"
    corrections_path: Path = Path("data/corrections.json")
    popularity_db_path: Path = Path("data/popularity.duckdb")
    popularity_seed_path: Path = Path("data/popularity_seed.sql")

    enable_query: bool = True
    enable_corrections: bool = True
    enable_dbt: bool = True
    enable_popularity: bool = True

    @property
    def manifest_path(self) -> Path:
        """Backward-compatible path accessor (only meaningful for local files)."""
        return Path(self.manifest_source)


def load_config(config_path: Path | None = None) -> Config:
    """Load configuration with precedence: env vars > config.toml > defaults.

    Args:
        config_path: Explicit path to config.toml. If None, checks
                     MCP_MEMORY_CONFIG env var, then ~/.mcp-memory/config.toml.

    Returns:
        Resolved Config instance.
    """
    cfg = Config()

    # 1. Load config.toml if it exists
    toml_path = config_path or Path(os.environ.get("MCP_MEMORY_CONFIG", DEFAULT_CONFIG_PATH))
    if toml_path.exists():
        with open(toml_path, "rb") as f:
            toml = tomllib.load(f)

        paths = toml.get("paths", {})
        features = toml.get("features", {})

        if "duckdb" in paths:
            cfg.duckdb_path = Path(paths["duckdb"])
        if "manifest" in paths:
            cfg.manifest_source = paths["manifest"]
        if "corrections" in paths:
            cfg.corrections_path = Path(paths["corrections"])
        if "popularity_db" in paths:
            cfg.popularity_db_path = Path(paths["popularity_db"])
        if "popularity_seed" in paths:
            cfg.popularity_seed_path = Path(paths["popularity_seed"])

        if "query" in features:
            cfg.enable_query = features["query"]
        if "corrections" in features:
            cfg.enable_corrections = features["corrections"]
        if "dbt" in features:
            cfg.enable_dbt = features["dbt"]
        if "popularity" in features:
            cfg.enable_popularity = features["popularity"]

    # 2. Env vars override config.toml
    data_dir = os.environ.get("MCP_MEMORY_DATA_DIR")

    if v := os.environ.get("MCP_MEMORY_DUCKDB_PATH"):
        cfg.duckdb_path = Path(v)
    elif data_dir:
        cfg.duckdb_path = Path(data_dir) / "jaffle_shop" / "jaffle_shop.duckdb"

    if v := os.environ.get("MCP_MEMORY_MANIFEST_PATH"):
        cfg.manifest_source = v

    if v := os.environ.get("MCP_MEMORY_CORRECTIONS_PATH"):
        cfg.corrections_path = Path(v)
    elif data_dir:
        cfg.corrections_path = Path(data_dir) / "corrections.json"

    if v := os.environ.get("MCP_MEMORY_POPULARITY_DB"):
        cfg.popularity_db_path = Path(v)
    elif data_dir:
        cfg.popularity_db_path = Path(data_dir) / "popularity.duckdb"

    if v := os.environ.get("MCP_MEMORY_POPULARITY_SEED"):
        cfg.popularity_seed_path = Path(v)
    elif data_dir:
        cfg.popularity_seed_path = Path(data_dir) / "popularity_seed.sql"

    for env_key, attr in [
        ("MCP_MEMORY_QUERY", "enable_query"),
        ("MCP_MEMORY_CORRECTIONS", "enable_corrections"),
        ("MCP_MEMORY_DBT", "enable_dbt"),
        ("MCP_MEMORY_POPULARITY", "enable_popularity"),
    ]:
        if v := os.environ.get(env_key):
            setattr(cfg, attr, v.lower() == "true")

    return cfg

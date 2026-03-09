"""MCP server with memory layer: corrections, dbt context, and popularity tracking."""

import duckdb
from mcp.server.fastmcp import FastMCP

from .config import load_config
from .corrections import CorrectionsStore
from .dbt_context import DbtManifest
from .manifest_resolver import resolve_manifest
from .popularity import PopularityTracker

# Load configuration (env vars > config.toml > defaults)
cfg = load_config()

# Initialize stores
corrections_store = CorrectionsStore(cfg.corrections_path) if cfg.enable_corrections else None

# Resolve manifest from local file or URL
dbt_manifest = None
if cfg.enable_dbt:
    manifest_dict = resolve_manifest(cfg.manifest_source)
    if manifest_dict:
        dbt_manifest = DbtManifest(manifest_dict=manifest_dict)
popularity_tracker = PopularityTracker(cfg.popularity_db_path) if cfg.enable_popularity else None

# Seed popularity data if enabled and table is empty
if popularity_tracker and cfg.popularity_seed_path.exists():
    count = popularity_tracker.db.execute("SELECT count(*) FROM table_popularity").fetchone()
    if count and count[0] == 0:
        popularity_tracker.seed(cfg.popularity_seed_path)

# Create MCP server
mcp = FastMCP(
    "mcp-memory",
    instructions="MCP server with memory layer for better text-to-SQL",
)


# --- Query tool ---

if cfg.enable_query:

    @mcp.tool()
    def query(sql: str) -> str:
        """Execute a SQL query against the DuckDB database.

        Args:
            sql: The SQL query to execute (DuckDB dialect).

        Returns:
            Query results as formatted text, or an error message.
        """
        try:
            conn = duckdb.connect(str(cfg.duckdb_path), read_only=True)
            result = conn.execute(sql)
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            conn.close()
        except Exception as e:
            error_msg = str(e)
            if dbt_manifest:
                error_msg = dbt_manifest.enrich_error(error_msg, sql)
            return f"Query error: {error_msg}"

        # Record patterns for popularity tracking
        if popularity_tracker:
            try:
                popularity_tracker.record_query(sql)
            except Exception:
                pass  # Never let tracking fail the query

        # Format output
        if not rows:
            return "Query returned no results."

        # Build a simple table
        col_widths = [len(c) for c in columns]
        for row in rows[:50]:  # Limit display to 50 rows
            for i, val in enumerate(row):
                col_widths[i] = max(col_widths[i], len(str(val)))

        header = " | ".join(c.ljust(col_widths[i]) for i, c in enumerate(columns))
        separator = "-+-".join("-" * w for w in col_widths)
        lines = [header, separator]
        for row in rows[:50]:
            lines.append(" | ".join(str(v).ljust(col_widths[i]) for i, v in enumerate(row)))

        output = "\n".join(lines)
        if len(rows) > 50:
            output += f"\n\n... ({len(rows)} total rows, showing first 50)"

        return output


# --- Corrections tools ---

if cfg.enable_corrections and corrections_store:

    @mcp.tool()
    def get_corrections(question: str, tables: list[str] | None = None) -> str:
        """Retrieve relevant corrections for a natural language question.

        Call this BEFORE writing SQL to avoid common mistakes.

        Args:
            question: The user's natural language question.
            tables: Optional list of table names to filter corrections by.

        Returns:
            Relevant corrections as formatted text, or empty if none match.
        """
        return corrections_store.get_corrections(question, tables)

    @mcp.tool()
    def save_correction(
        correction: str,
        tables: list[str],
        columns: list[str] | None = None,
        keywords: list[str] | None = None,
    ) -> str:
        """Save a new correction for future queries.

        Use this when you discover something non-obvious about the schema
        that could trip up future queries.

        Args:
            correction: The correction text explaining what to watch out for.
            tables: Which tables this correction applies to.
            columns: Which columns are involved (optional).
            keywords: Search keywords that should trigger this correction (optional).
        """
        return corrections_store.save_correction(correction, tables, columns, keywords)


# --- dbt context tools ---

if cfg.enable_dbt and dbt_manifest:

    @mcp.tool()
    def get_dbt_context(table_name: str) -> str:
        """Get dbt model context for a table: description, columns, lineage, tests.

        Call this to understand what a table contains and how it relates to other tables.

        Args:
            table_name: Name of the model/table to look up.

        Returns:
            Formatted context about the model, or suggestions if not found.
        """
        return dbt_manifest.get_context(table_name)

    @mcp.tool()
    def get_model_sql(table_name: str) -> str:
        """Get the full raw SQL for a dbt model.

        Use this when you need to understand the exact transformations, column
        renames, CASE statements, or business logic in a model's SQL.

        Args:
            table_name: Name of the model to get SQL for.

        Returns:
            The complete SQL source code of the model.
        """
        return dbt_manifest.get_model_sql(table_name)

    @mcp.tool()
    def list_dbt_models(search: str | None = None) -> str:
        """List all available dbt models with brief descriptions.

        Call this to discover which tables are available.
        Pass a search keyword to filter by model name, description, or column names.

        Args:
            search: Optional keyword to filter models by name, description, or column names.

        Returns:
            Table of model names, materializations, and descriptions.
        """
        return dbt_manifest.list_models(search)


# --- Popularity tools ---

if cfg.enable_popularity and popularity_tracker:

    @mcp.tool()
    def get_popular_context(tables: list[str] | None = None) -> str:
        """Get query popularity stats and common join patterns.

        Call this to understand which tables and joins are commonly used.

        Args:
            tables: Optional table names to get specific patterns for.
                    If omitted, returns top tables overall.

        Returns:
            Popularity stats and recommended join patterns.
        """
        return popularity_tracker.get_popular_context(tables)


def main():
    mcp.run()


if __name__ == "__main__":
    main()

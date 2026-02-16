"""Personal MCP server: MotherDuck query tools + memory layer (corrections, dbt context).

Uses mcp-server-motherduck as the base (execute_query, list_tables, etc.) and
registers memory tools on top for schema corrections and dbt model context.
"""

import os
from pathlib import Path

import click

from .corrections import CorrectionsStore
from .dbt_context import DbtManifest

# Memory layer config
MDW_DIR = Path(os.environ.get("MCP_MEMORY_MDW_DIR", Path.home() / ".mcp-memory-mdw"))
MANIFEST_PATH = Path(os.environ.get("MCP_MEMORY_MANIFEST_PATH", MDW_DIR / "manifest.json"))
CORRECTIONS_PATH = Path(os.environ.get("MCP_MEMORY_CORRECTIONS_PATH", MDW_DIR / "corrections.json"))

MEMORY_INSTRUCTIONS = """
## IMPORTANT: Required Workflow for Data Questions

This server has a memory layer with dbt model context and corrections. You MUST follow
this workflow for every data question:

1. **Call `get_corrections`** with the user's question to get schema tips and gotchas
2. **Call `list_dbt_models`** to discover available tables, then **`get_dbt_context`**
   for the relevant models. The dbt catalog is the source of truth for this warehouse —
   it has 140+ curated models with descriptions, column definitions, and lineage.
   Do NOT use `duckdb_tables()`, `duckdb_columns()`, `information_schema`, or
   `DESCRIBE`/`SUMMARIZE` to explore tables. These return raw system metadata without
   business context and will lead you to wrong tables. Only use system catalogs for
   system-level questions (storage, permissions, database internals).
3. **Write and run SQL** using `execute_query`
4. **Call `save_correction`** if you discover something non-obvious about the schema
   (misleading names, stale data, unexpected formats, non-obvious joins)

### Memory Layer Tools

- `get_corrections`: Schema corrections and tips from previous sessions. **Call first.**
- `save_correction`: Save a schema insight for future sessions.
- `get_dbt_context`: Columns, description, lineage, and tests for a dbt model.
- `get_model_sql`: Full SQL source of a dbt model (transformations, business logic).
- `list_dbt_models`: All dbt-managed models with descriptions.
"""


def register_resources(mcp, corrections_store, dbt_manifest):
    """Register MCP resources for discoverability.

    Resources make the server visible when clients list available resources,
    and provide ambient context without needing tool calls.
    """

    @mcp.resource(
        "motherduck://overview",
        name="MotherDuck Memory Layer",
        description=(
            "Overview of this MotherDuck MCP server with memory layer. "
            "Describes available tools, the recommended workflow for data questions, "
            "and how corrections and dbt context improve SQL generation."
        ),
        mime_type="text/markdown",
    )
    def overview() -> str:
        tools = ["execute_query", "list_databases", "list_tables", "list_columns"]
        memory_tools = []
        if corrections_store:
            memory_tools += ["get_corrections", "save_correction"]
        if dbt_manifest:
            memory_tools += ["list_dbt_models", "get_dbt_context", "get_model_sql"]

        sections = [
            "# MotherDuck MCP Server with Memory Layer\n",
            "## Query Tools\n",
            "\n".join(f"- `{t}`" for t in tools),
        ]
        if memory_tools:
            sections += [
                "\n## Memory Layer Tools\n",
                "\n".join(f"- `{t}`" for t in memory_tools),
                "\n## Recommended Workflow\n",
                MEMORY_INSTRUCTIONS,
            ]
        return "\n".join(sections)

    if dbt_manifest:

        @mcp.resource(
            "motherduck://dbt-models",
            name="dbt Models",
            description=(
                "List of all dbt-managed models in the data warehouse with descriptions. "
                "Use this to discover what tables are available before querying."
            ),
            mime_type="text/plain",
        )
        def dbt_models() -> str:
            return dbt_manifest.list_models()

    if corrections_store:

        @mcp.resource(
            "motherduck://corrections",
            name="Schema Corrections",
            description=(
                "Known schema gotchas, naming quirks, and tips collected from previous sessions. "
                "Review these to avoid common mistakes when writing SQL."
            ),
            mime_type="text/plain",
        )
        def corrections() -> str:
            return corrections_store.list_all()


def _patch_tool_descriptions(mcp, has_dbt: bool) -> None:
    """Patch MotherDuck tool descriptions to steer toward memory layer tools.

    This ensures any MCP client sees the guidance in the tool list itself,
    without needing external instructions (CLAUDE.md, hooks, etc.).
    """
    patches = {
        "execute_query": (
            "Execute a SQL query on MotherDuck/DuckDB. Use this tool for ALL SQL queries "
            "— do not use the duckdb CLI or shell commands to query data. "
            "Before writing SQL, call get_corrections and get_dbt_context for schema context."
        ),
    }
    if has_dbt:
        patches["list_tables"] = (
            "List tables and views in a database. "
            "NOTE: Prefer list_dbt_models and get_dbt_context for schema exploration — "
            "they include column descriptions, business context, and lineage. "
            "Only use list_tables for system-level queries or non-dbt tables."
        )
        patches["list_columns"] = (
            "List columns of a table or view with types. "
            "NOTE: Prefer get_dbt_context for column exploration — "
            "it includes column descriptions, tests, and business context. "
            "Only use list_columns for system-level queries or non-dbt tables."
        )

    tools = mcp._tool_manager._tools
    for name, new_desc in patches.items():
        if name in tools:
            tools[name].description = new_desc


def register_memory_tools(mcp, corrections_store, dbt_manifest):
    """Register memory layer tools onto an existing FastMCP server."""

    if corrections_store:

        @mcp.tool(
            name="get_corrections",
            title="Get Corrections",
            description=(
                "Get schema corrections and tips before writing SQL. "
                "ALWAYS call this first when you receive a data question — it returns "
                "known gotchas, naming quirks, and insights from previous sessions."
            ),
            annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        )
        def get_corrections(question: str, tables: list[str] | None = None) -> str:
            """Retrieve relevant corrections for a natural language question.

            Args:
                question: The user's natural language question.
                tables: Optional list of table names to filter corrections by.

            Returns:
                Relevant corrections as formatted text, or empty if none match.
            """
            return corrections_store.get_corrections(question, tables)

        @mcp.tool(
            name="save_correction",
            title="Save Correction",
            description=(
                "Save a schema insight for future sessions. Call this when you discover "
                "something non-obvious: misleading column names, unexpected data formats, "
                "stale tables, surprising joins, or naming conventions that differ from "
                "what you'd expect. These corrections help avoid the same mistake next time."
            ),
            annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        )
        def save_correction(
            correction: str,
            tables: list[str],
            columns: list[str] | None = None,
            keywords: list[str] | None = None,
        ) -> str:
            """Save a new correction for future queries.

            Args:
                correction: The correction text explaining what to watch out for.
                tables: Which tables this correction applies to.
                columns: Which columns are involved (optional).
                keywords: Search keywords that should trigger this correction (optional).
            """
            return corrections_store.save_correction(correction, tables, columns, keywords)

    if dbt_manifest:

        @mcp.tool(
            name="get_dbt_context",
            title="Get dbt Model Context",
            description=(
                "Get dbt model context: description, columns, upstream lineage, and tests. "
                "Call this to understand what a table contains and how it relates to other tables "
                "before writing a query."
            ),
            annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        )
        def get_dbt_context(table_name: str) -> str:
            """Get dbt model context for a table.

            Args:
                table_name: Name of the model/table to look up.

            Returns:
                Formatted context about the model, or suggestions if not found.
            """
            return dbt_manifest.get_context(table_name)

        @mcp.tool(
            name="get_model_sql",
            title="Get Model SQL",
            description=(
                "Get the full raw SQL for a dbt model. Use when you need to understand "
                "exact transformations, column renames, CASE statements, or business logic."
            ),
            annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        )
        def get_model_sql(table_name: str) -> str:
            """Get the full raw SQL for a dbt model.

            Args:
                table_name: Name of the model to get SQL for.

            Returns:
                The complete SQL source code of the model.
            """
            return dbt_manifest.get_model_sql(table_name)

        @mcp.tool(
            name="list_dbt_models",
            title="List dbt Models",
            description=(
                "List all dbt-managed models with descriptions and materialization type. "
                "Use this to discover which tables are available in the data warehouse."
            ),
            annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        )
        def list_dbt_models() -> str:
            """List all available dbt models with brief descriptions.

            Returns:
                Table of model names, materializations, and descriptions.
            """
            return dbt_manifest.list_models()


@click.command()
@click.option("--db-path", default="md:", envvar="MCP_DB_PATH")
@click.option("--motherduck-token", default=None, envvar=["motherduck_token", "MOTHERDUCK_TOKEN"])
@click.option("--read-write", is_flag=True, envvar="MCP_READ_WRITE", help="Enable write access")
@click.option("--manifest-path", default=str(MANIFEST_PATH), envvar="MCP_MEMORY_MANIFEST_PATH")
@click.option(
    "--corrections-path", default=str(CORRECTIONS_PATH), envvar="MCP_MEMORY_CORRECTIONS_PATH"
)
@click.option("--transport", type=click.Choice(["stdio", "http"]), default="stdio")
def main(
    db_path: str,
    motherduck_token: str | None,
    read_write: bool,
    manifest_path: str,
    corrections_path: str,
    transport: str,
) -> None:
    """Personal MotherDuck MCP server with memory layer."""
    from mcp_server_motherduck.server import create_mcp_server

    # Create the base MotherDuck MCP server
    mcp = create_mcp_server(
        db_path=db_path,
        motherduck_token=motherduck_token,
        read_only=not read_write,
    )

    # Prepend memory layer instructions so they take priority over base schema exploration
    mcp.instructions = MEMORY_INSTRUCTIONS + (mcp.instructions or "")

    # Initialize memory stores
    manifest = Path(manifest_path)
    corrections = Path(corrections_path)

    dbt_manifest = DbtManifest(manifest) if manifest.exists() else None
    corrections_store = CorrectionsStore(corrections) if corrections.exists() else None

    if dbt_manifest:
        click.echo(f"[mdw-memory] dbt manifest: {len(dbt_manifest.models)} models", err=True)
    if corrections_store:
        click.echo(
            f"[mdw-memory] corrections: {len(corrections_store.corrections)} loaded", err=True
        )

    # Register memory tools and resources onto the base server
    register_memory_tools(mcp, corrections_store, dbt_manifest)
    register_resources(mcp, corrections_store, dbt_manifest)

    # Patch MotherDuck tool descriptions to steer toward memory tools
    _patch_tool_descriptions(mcp, has_dbt=dbt_manifest is not None)

    tool_count = len(mcp._tool_manager._tools)
    click.echo(f"[mdw-memory] server ready with {tool_count} tools", err=True)

    mcp.run(transport=transport)


if __name__ == "__main__":
    main()

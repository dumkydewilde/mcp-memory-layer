"""MCP server with a local memory layer and optional MotherDuck remote proxying."""

from __future__ import annotations

import inspect
import logging
from typing import Annotated, Any

import anyio
import duckdb
import mcp.types as types
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .config import Config, load_config
from .corrections import CorrectionsStore
from .dbt_context import DbtManifest
from .manifest_resolver import resolve_manifest
from .motherduck_client import (
    MotherDuckDatabasesResult,
    MotherDuckQueryResult,
    MotherDuckRemoteClient,
)
from .popularity import PopularityTracker

logger = logging.getLogger(__name__)


def create_server(
    config: Config | None = None,
    motherduck_client: MotherDuckRemoteClient | None = None,
) -> FastMCP:
    """Create the MCP server with local memory tools and optional remote proxy tools."""
    cfg = config or load_config()

    corrections_store = (
        CorrectionsStore(cfg.corrections_path) if cfg.enable_corrections else None
    )
    dbt_manifest = _load_dbt_manifest(cfg)
    popularity_tracker = (
        PopularityTracker(cfg.popularity_db_path) if cfg.enable_popularity else None
    )
    _seed_popularity_tracker(cfg, popularity_tracker)

    remote_client = motherduck_client or MotherDuckRemoteClient.from_config(cfg)
    use_remote_query = remote_client is not None

    mcp = FastMCP("mcp-memory", instructions=_build_instructions(use_remote_query))

    if cfg.enable_query:
        if use_remote_query:
            _register_remote_query_tools(mcp, cfg, remote_client, dbt_manifest, popularity_tracker)
            _register_remote_catalog_tools(mcp, remote_client)
            diagnostics = _register_dynamic_remote_tools(mcp, remote_client)
            _register_remote_diagnostic_tools(mcp, remote_client, diagnostics)
        else:
            _register_local_query_tool(mcp, cfg, dbt_manifest, popularity_tracker)

    if cfg.enable_corrections and corrections_store:
        _register_corrections_tools(mcp, corrections_store)

    if cfg.enable_dbt and dbt_manifest:
        _register_dbt_tools(mcp, dbt_manifest)

    if cfg.enable_popularity and popularity_tracker:
        _register_popularity_tools(mcp, popularity_tracker)

    return mcp


def _build_instructions(use_remote_query: bool) -> str:
    query_target = (
        "MotherDuck databases through the wrapped remote MCP"
        if use_remote_query
        else "the local DuckDB database"
    )
    return (
        "MCP memory layer for text-to-SQL. Required workflow for every data question: "
        "1) get_corrections with the user's question, "
        "2) list_dbt_models to find relevant models, then get_dbt_context for each, "
        f"3) query against {query_target} using dbt models (not raw tables) when they exist, "
        "4) save_correction if you discover something non-obvious about the schema."
    )


def _load_dbt_manifest(cfg: Config) -> DbtManifest | None:
    if not cfg.enable_dbt:
        return None

    manifest_dict = resolve_manifest(cfg.manifest_source)
    if not manifest_dict:
        return None

    return DbtManifest(manifest_dict=manifest_dict)


def _seed_popularity_tracker(cfg: Config, popularity_tracker: PopularityTracker | None) -> None:
    if not popularity_tracker or not cfg.popularity_seed_path.exists():
        return

    count = popularity_tracker.db.execute("SELECT count(*) FROM table_popularity").fetchone()
    if count and count[0] == 0:
        popularity_tracker.seed(cfg.popularity_seed_path)


def _register_local_query_tool(
    mcp: FastMCP,
    cfg: Config,
    dbt_manifest: DbtManifest | None,
    popularity_tracker: PopularityTracker | None,
) -> None:
    @mcp.tool()
    def query(sql: str) -> str:
        """Execute a SQL query against the local DuckDB database.

        Always call get_corrections and get_dbt_context BEFORE writing the query.
        Prefer querying dbt models over raw tables when they exist.
        Always use fully qualified names (database.schema.table) when available.
        """
        try:
            conn = duckdb.connect(str(cfg.duckdb_path), read_only=True)
            result = conn.execute(sql)
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            conn.close()
        except Exception as exc:
            error_msg = str(exc)
            if dbt_manifest:
                error_msg = dbt_manifest.enrich_error(error_msg, sql)
            return f"Query error: {error_msg}"

        if popularity_tracker:
            try:
                popularity_tracker.record_query(sql)
            except Exception:
                pass

        if not rows:
            return "Query returned no results."

        col_widths = [len(c) for c in columns]
        for row in rows[:50]:
            for index, value in enumerate(row):
                col_widths[index] = max(col_widths[index], len(str(value)))

        header = " | ".join(column.ljust(col_widths[i]) for i, column in enumerate(columns))
        separator = "-+-".join("-" * width for width in col_widths)
        lines = [header, separator]
        for row in rows[:50]:
            lines.append(" | ".join(str(value).ljust(col_widths[i]) for i, value in enumerate(row)))

        output = "\n".join(lines)
        if len(rows) > 50:
            output += f"\n\n... ({len(rows)} total rows, showing first 50)"

        return output


def _register_remote_query_tools(
    mcp: FastMCP,
    cfg: Config,
    remote_client: MotherDuckRemoteClient,
    dbt_manifest: DbtManifest | None,
    popularity_tracker: PopularityTracker | None,
) -> None:
    @mcp.tool()
    async def query(database: str, sql: str) -> MotherDuckQueryResult:
        """Execute a read-only SQL query against MotherDuck.

        Always call get_corrections and get_dbt_context BEFORE writing the query.
        Prefer querying dbt models over raw tables when they exist.
        """
        result = await remote_client.query(database=database, sql=sql)
        _record_popularity(popularity_tracker, sql)
        return _enrich_query_result(result, dbt_manifest, sql)

    if cfg.enable_motherduck_query_rw:

        @mcp.tool()
        async def query_rw(sql: str, database: str | None = None) -> MotherDuckQueryResult:
            """Execute a read-write SQL statement against MotherDuck.

            Use this only when you need to create, update, or delete data or schema.
            """
            result = await remote_client.query_rw(sql=sql, database=database)
            _record_popularity(popularity_tracker, sql)
            return _enrich_query_result(result, dbt_manifest, sql)


def _register_remote_catalog_tools(
    mcp: FastMCP,
    remote_client: MotherDuckRemoteClient,
) -> None:
    @mcp.tool()
    async def list_databases() -> MotherDuckDatabasesResult:
        """List all databases accessible through the wrapped MotherDuck MCP server."""
        return await remote_client.list_databases()

    @mcp.tool()
    async def list_tables(database: str, schema: str | None = None) -> dict[str, object]:
        """List tables and views in a MotherDuck database."""
        return await remote_client.list_tables(database=database, schema=schema)

    @mcp.tool()
    async def list_columns(
        database: str,
        table: str,
        schema: str | None = None,
    ) -> dict[str, object]:
        """List columns for a MotherDuck table or view."""
        return await remote_client.list_columns(database=database, table=table, schema=schema)

    @mcp.tool()
    async def search_catalog(query: str) -> dict[str, object]:
        """Search the MotherDuck catalog for matching databases, tables, and columns."""
        return await remote_client.search_catalog(query=query)

    @mcp.tool()
    async def ask_docs_question(question: str) -> dict[str, object]:
        """Ask the MotherDuck documentation assistant a question."""
        return await remote_client.ask_docs_question(question=question)


def _register_dynamic_remote_tools(
    mcp: FastMCP,
    remote_client: MotherDuckRemoteClient,
) -> dict[str, object]:
    excluded_tool_names = {
        "query",
        "query_rw",
        "list_databases",
        "list_tables",
        "list_columns",
        "search_catalog",
        "ask_docs_question",
    }
    diagnostics: dict[str, object] = {
        "discoverySucceeded": False,
        "error": None,
        "upstreamToolCount": 0,
        "registeredDynamicTools": [],
        "skippedWrappedTools": sorted(excluded_tool_names),
    }

    try:
        upstream_tools = anyio.run(remote_client.list_available_tools)
    except Exception as exc:
        diagnostics["error"] = str(exc)
        logger.warning("MotherDuck upstream tool discovery failed: %s", exc)
        return diagnostics

    diagnostics["discoverySucceeded"] = True
    diagnostics["upstreamToolCount"] = len(upstream_tools)

    for tool in upstream_tools:
        if tool.name in excluded_tool_names:
            continue

        proxy_fn = _build_dynamic_proxy_tool(remote_client, tool)
        mcp.add_tool(
            proxy_fn,
            name=tool.name,
            title=tool.title,
            description=tool.description,
            annotations=tool.annotations,
            icons=tool.icons,
            meta=tool.meta,
            structured_output=True,
        )
        registered_tools = diagnostics["registeredDynamicTools"]
        assert isinstance(registered_tools, list)
        registered_tools.append(tool.name)

    logger.info(
        "MotherDuck upstream tool discovery succeeded: %s tools, %s dynamically registered",
        diagnostics["upstreamToolCount"],
        len(diagnostics["registeredDynamicTools"]),
    )
    return diagnostics


def _register_remote_diagnostic_tools(
    mcp: FastMCP,
    remote_client: MotherDuckRemoteClient,
    startup_diagnostics: dict[str, object],
) -> None:
    @mcp.tool()
    def get_motherduck_wrapper_status() -> dict[str, object]:
        """Show startup discovery status for the wrapped MotherDuck MCP server."""
        return dict(startup_diagnostics)

    @mcp.tool()
    async def list_upstream_motherduck_tools(refresh: bool = False) -> dict[str, object]:
        """List tools exposed by the upstream MotherDuck MCP server."""
        if not refresh:
            return {
                **dict(startup_diagnostics),
                "upstreamTools": sorted(
                    list(startup_diagnostics["skippedWrappedTools"])
                    + list(startup_diagnostics["registeredDynamicTools"])
                ),
            }

        try:
            tools = await remote_client.list_available_tools()
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
            }

        return {
            "success": True,
            "count": len(tools),
            "tools": [
                {
                    "name": tool.name,
                    "title": tool.title,
                    "description": tool.description,
                }
                for tool in tools
            ],
        }


def _register_corrections_tools(mcp: FastMCP, corrections_store: CorrectionsStore) -> None:
    @mcp.tool()
    def get_corrections(question: str, tables: list[str] | None = None) -> str:
        """Retrieve relevant corrections for a natural language question."""
        return corrections_store.get_corrections(question, tables)

    @mcp.tool()
    def save_correction(
        correction: str,
        tables: list[str],
        columns: list[str] | None = None,
        keywords: list[str] | None = None,
    ) -> str:
        """Save a new correction for future queries."""
        return corrections_store.save_correction(correction, tables, columns, keywords)


def _register_dbt_tools(mcp: FastMCP, dbt_manifest: DbtManifest) -> None:
    @mcp.tool()
    def get_dbt_context(table_name: str) -> str:
        """Get dbt model definition, columns, lineage, and tests for a model."""
        return dbt_manifest.get_context(table_name)

    @mcp.tool()
    def get_model_sql(table_name: str) -> str:
        """Get the full raw SQL for a dbt model."""
        return dbt_manifest.get_model_sql(table_name)

    @mcp.tool()
    def list_dbt_models(search: str | None = None) -> str:
        """List dbt models, tables, and metrics with their schema."""
        return dbt_manifest.list_models(search)


def _register_popularity_tools(mcp: FastMCP, popularity_tracker: PopularityTracker) -> None:
    @mcp.tool()
    def get_popular_context(tables: list[str] | None = None) -> str:
        """Get query popularity stats and common join patterns."""
        return popularity_tracker.get_popular_context(tables)


def _enrich_query_result(
    result: MotherDuckQueryResult,
    dbt_manifest: DbtManifest | None,
    sql: str,
) -> MotherDuckQueryResult:
    if not result.success and result.error and dbt_manifest:
        result.error = dbt_manifest.enrich_error(result.error, sql)
    return result


def _record_popularity(popularity_tracker: PopularityTracker | None, sql: str) -> None:
    if not popularity_tracker:
        return

    try:
        popularity_tracker.record_query(sql)
    except Exception:
        pass


def _build_dynamic_proxy_tool(
    remote_client: MotherDuckRemoteClient,
    tool: types.Tool,
):
    properties = tool.inputSchema.get("properties", {})
    required = set(tool.inputSchema.get("required", []))
    signature_parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {"return": dict[str, Any]}

    for name, schema in properties.items():
        annotation = _annotation_from_schema(schema)
        description = schema.get("description")
        if description:
            annotation = Annotated[annotation, Field(description=description)]

        if name in required:
            default = inspect.Parameter.empty
        else:
            default = schema.get("default", None)

        signature_parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=annotation,
                default=default,
            )
        )
        annotations[name] = annotation

    async def proxy_tool(**kwargs: Any) -> dict[str, Any]:
        arguments = kwargs or None
        return await remote_client.call_tool_json(tool.name, arguments)

    proxy_tool.__name__ = tool.name
    proxy_tool.__doc__ = tool.description or f"Pass through to MotherDuck tool '{tool.name}'."
    proxy_tool.__signature__ = inspect.Signature(
        signature_parameters,
        return_annotation=dict[str, Any],
    )
    proxy_tool.__annotations__ = annotations

    return proxy_tool


def _annotation_from_schema(schema: dict[str, Any]) -> Any:
    if "anyOf" in schema:
        variants = [variant for variant in schema["anyOf"] if variant.get("type") != "null"]
        if len(variants) == 1:
            return _annotation_from_schema(variants[0]) | None
        return Any

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        non_null_types = [item for item in schema_type if item != "null"]
        if len(non_null_types) == 1:
            inner = _annotation_from_schema({**schema, "type": non_null_types[0]})
            if "null" in schema_type:
                return inner | None
            return inner
        return Any

    if schema_type == "string":
        return str
    if schema_type == "integer":
        return int
    if schema_type == "number":
        return float
    if schema_type == "boolean":
        return bool
    if schema_type == "array":
        item_schema = schema.get("items", {})
        return list[_annotation_from_schema(item_schema)]
    if schema_type == "object":
        return dict[str, Any]

    return Any


def main() -> None:
    create_server().run()


if __name__ == "__main__":
    main()

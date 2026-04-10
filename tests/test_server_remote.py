"""Tests for wrapping MotherDuck remote MCP tools."""

import anyio
import mcp.types as types

from mcp_memory.config import Config
from mcp_memory.motherduck_client import MotherDuckDatabasesResult, MotherDuckQueryResult
from mcp_memory.server import create_server


class FakeMotherDuckRemoteClient:
    async def list_available_tools(self) -> list[types.Tool]:
        return [
            types.Tool(
                name="list_shares",
                description="List available shares.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="create_dive",
                description="Create a MotherDuck Dive.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Dive name."},
                        "description": {
                            "type": ["string", "null"],
                            "description": "Optional description.",
                        },
                    },
                    "required": ["name"],
                },
            ),
        ]

    async def list_databases(self) -> MotherDuckDatabasesResult:
        return MotherDuckDatabasesResult(
            success=True,
            databases=[{"alias": "analytics", "is_attached": True, "type": "motherduck"}],
        )

    async def list_tables(self, database: str, schema: str | None = None) -> dict[str, object]:
        return {
            "success": True,
            "database": database,
            "schema": schema or "main",
            "tables": [{"name": "orders", "schema": schema or "main"}],
        }

    async def list_columns(
        self,
        database: str,
        table: str,
        schema: str | None = None,
    ) -> dict[str, object]:
        return {
            "success": True,
            "database": database,
            "table": table,
            "schema": schema or "main",
            "columns": [{"name": "order_id", "type": "BIGINT"}],
        }

    async def search_catalog(self, query: str) -> dict[str, object]:
        return {
            "success": True,
            "query": query,
            "results": [{"objectType": "table", "path": "analytics.main.orders"}],
        }

    async def ask_docs_question(self, question: str) -> dict[str, object]:
        return {
            "success": True,
            "question": question,
            "answer": "Use fully qualified names for cross-database queries.",
        }

    async def query(self, database: str, sql: str) -> MotherDuckQueryResult:
        return MotherDuckQueryResult(
            success=True,
            columns=["answer"],
            columnTypes=["INTEGER"],
            rows=[[42]],
            rowCount=1,
        )

    async def query_rw(self, sql: str, database: str | None = None) -> MotherDuckQueryResult:
        return MotherDuckQueryResult(
            success=True,
            columns=[],
            columnTypes=[],
            rows=[],
            rowCount=0,
        )

    async def call_tool_json(
        self,
        name: str,
        arguments: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if name == "list_shares":
            return {"success": True, "shares": [{"name": "marketing_metrics"}]}
        if name == "create_dive":
            return {
                "success": True,
                "dive": {
                    "name": arguments["name"],
                    "description": arguments.get("description"),
                },
            }
        raise AssertionError(f"Unexpected dynamic tool call: {name}")


def test_remote_server_registers_motherduck_tools(tmp_path):
    """When configured with a MotherDuck client, the server exposes remote tools."""
    cfg = Config(
        duckdb_path=tmp_path / "unused.duckdb",
        manifest_source=str(tmp_path / "missing-manifest.json"),
        corrections_path=tmp_path / "corrections.json",
        popularity_db_path=tmp_path / "popularity.duckdb",
        popularity_seed_path=tmp_path / "missing-seed.sql",
        motherduck_mcp_url="https://api.motherduck.com/mcp",
        enable_dbt=False,
        enable_corrections=False,
        enable_popularity=False,
        enable_motherduck_query_rw=True,
    )
    server = create_server(config=cfg, motherduck_client=FakeMotherDuckRemoteClient())

    tools = anyio.run(server.list_tools)
    names = {tool.name for tool in tools}
    create_dive_tool = next(tool for tool in tools if tool.name == "create_dive")

    assert "query" in names
    assert "query_rw" in names
    assert "list_databases" in names
    assert "list_tables" in names
    assert "list_columns" in names
    assert "search_catalog" in names
    assert "ask_docs_question" in names
    assert "list_shares" in names
    assert "create_dive" in names
    assert "get_motherduck_wrapper_status" in names
    assert "list_upstream_motherduck_tools" in names
    assert create_dive_tool.inputSchema["required"] == ["name"]
    assert "description" in create_dive_tool.inputSchema["properties"]


def test_remote_query_tool_calls_motherduck(tmp_path):
    """The wrapped query tool forwards calls to the MotherDuck client."""
    cfg = Config(
        duckdb_path=tmp_path / "unused.duckdb",
        manifest_source=str(tmp_path / "missing-manifest.json"),
        corrections_path=tmp_path / "corrections.json",
        popularity_db_path=tmp_path / "popularity.duckdb",
        popularity_seed_path=tmp_path / "missing-seed.sql",
        motherduck_mcp_url="https://api.motherduck.com/mcp",
        enable_dbt=False,
        enable_corrections=False,
        enable_popularity=False,
    )
    server = create_server(config=cfg, motherduck_client=FakeMotherDuckRemoteClient())

    _content, structured = anyio.run(
        server.call_tool,
        "query",
        {"database": "analytics", "sql": "select 42 as answer"},
    )

    assert structured["success"] is True
    assert structured["rows"] == [[42]]


def test_dynamic_remote_tool_calls_passthrough(tmp_path):
    """Discovered MotherDuck tools are proxied through this server."""
    cfg = Config(
        duckdb_path=tmp_path / "unused.duckdb",
        manifest_source=str(tmp_path / "missing-manifest.json"),
        corrections_path=tmp_path / "corrections.json",
        popularity_db_path=tmp_path / "popularity.duckdb",
        popularity_seed_path=tmp_path / "missing-seed.sql",
        motherduck_mcp_url="https://api.motherduck.com/mcp",
        enable_dbt=False,
        enable_corrections=False,
        enable_popularity=False,
    )
    server = create_server(config=cfg, motherduck_client=FakeMotherDuckRemoteClient())

    _content, structured = anyio.run(server.call_tool, "list_shares", {})
    assert structured["success"] is True
    assert structured["shares"][0]["name"] == "marketing_metrics"

    _content, structured = anyio.run(
        server.call_tool,
        "create_dive",
        {"name": "Quarterly Review", "description": "Q1 summary"},
    )
    assert structured["success"] is True
    assert structured["dive"]["name"] == "Quarterly Review"


def test_remote_diagnostic_tools_report_upstream_state(tmp_path):
    """Diagnostics show both startup discovery state and refreshed upstream tools."""
    cfg = Config(
        duckdb_path=tmp_path / "unused.duckdb",
        manifest_source=str(tmp_path / "missing-manifest.json"),
        corrections_path=tmp_path / "corrections.json",
        popularity_db_path=tmp_path / "popularity.duckdb",
        popularity_seed_path=tmp_path / "missing-seed.sql",
        motherduck_mcp_url="https://api.motherduck.com/mcp",
        enable_dbt=False,
        enable_corrections=False,
        enable_popularity=False,
    )
    server = create_server(config=cfg, motherduck_client=FakeMotherDuckRemoteClient())

    _content, structured = anyio.run(server.call_tool, "get_motherduck_wrapper_status", {})
    assert structured["discoverySucceeded"] is True
    assert "create_dive" in structured["registeredDynamicTools"]

    _content, structured = anyio.run(
        server.call_tool,
        "list_upstream_motherduck_tools",
        {"refresh": True},
    )
    assert structured["success"] is True
    assert any(tool["name"] == "create_dive" for tool in structured["tools"])

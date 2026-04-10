"""Thin MCP client wrapper for the hosted MotherDuck remote server."""

from __future__ import annotations

import json
from typing import Any

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ContentBlock, TextContent
from pydantic import BaseModel, Field

from .config import Config


class MotherDuckDatabase(BaseModel):
    alias: str
    is_attached: bool
    type: str


class MotherDuckDatabasesResult(BaseModel):
    success: bool
    databases: list[MotherDuckDatabase] = Field(default_factory=list)
    error: str | None = None


class MotherDuckQueryResult(BaseModel):
    success: bool
    columns: list[str] = Field(default_factory=list)
    columnTypes: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    rowCount: int | None = None
    error: str | None = None
    errorType: str | None = None


class MotherDuckRemoteClient:
    """Proxy client for MotherDuck's hosted MCP server."""

    def __init__(self, url: str, headers: dict[str, str] | None = None):
        self.url = url
        self.headers = headers or {}

    @classmethod
    def from_config(cls, cfg: Config) -> MotherDuckRemoteClient | None:
        """Create a client when a remote MCP endpoint is configured."""
        if not cfg.motherduck_mcp_url:
            return None
        return cls(cfg.motherduck_mcp_url, headers=cfg.motherduck_headers)

    async def list_databases(self) -> MotherDuckDatabasesResult:
        payload = await self.call_tool_json("list_databases")
        return MotherDuckDatabasesResult.model_validate(payload)

    async def list_available_tools(self) -> list[types.Tool]:
        """List tools exposed by the upstream MotherDuck MCP server."""
        tools: list[types.Tool] = []
        cursor: str | None = None

        async with streamable_http_client(self.url, headers=self.headers) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                while True:
                    result = await session.list_tools(cursor=cursor)
                    tools.extend(result.tools)
                    if not result.nextCursor:
                        break
                    cursor = result.nextCursor

        return tools

    async def list_tables(
        self,
        database: str,
        schema: str | None = None,
    ) -> dict[str, Any]:
        arguments = {"database": database}
        if schema:
            arguments["schema"] = schema
        return await self.call_tool_json("list_tables", arguments)

    async def list_columns(
        self,
        database: str,
        table: str,
        schema: str | None = None,
    ) -> dict[str, Any]:
        arguments = {"database": database, "table": table}
        if schema:
            arguments["schema"] = schema
        return await self.call_tool_json("list_columns", arguments)

    async def search_catalog(
        self,
        query: str,
    ) -> dict[str, Any]:
        return await self.call_tool_json("search_catalog", {"query": query})

    async def ask_docs_question(self, question: str) -> dict[str, Any]:
        return await self.call_tool_json("ask_docs_question", {"question": question})

    async def query(self, database: str, sql: str) -> MotherDuckQueryResult:
        payload = await self.call_tool_json("query", {"database": database, "sql": sql})
        return MotherDuckQueryResult.model_validate(payload)

    async def query_rw(self, sql: str, database: str | None = None) -> MotherDuckQueryResult:
        arguments: dict[str, Any] = {"sql": sql}
        if database:
            arguments["database"] = database
        payload = await self.call_tool_json("query_rw", arguments)
        return MotherDuckQueryResult.model_validate(payload)

    async def call_tool_json(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call an upstream MotherDuck MCP tool and normalize the result."""
        async with streamable_http_client(self.url, headers=self.headers) as (
            read_stream,
            write_stream,
            _get_session_id,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments=arguments)
        return self._normalize_tool_result(name, result)

    def _normalize_tool_result(self, name: str, result: CallToolResult) -> dict[str, Any]:
        if result.structuredContent is not None:
            return dict(result.structuredContent)

        text = self._content_to_text(result.content)
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                return parsed

        if result.isError:
            return {
                "success": False,
                "error": text or f"MotherDuck tool '{name}' returned an error.",
                "errorType": "UpstreamToolError",
            }

        return {
            "success": True,
            "content": text,
        }

    @staticmethod
    def _content_to_text(content: list[ContentBlock]) -> str:
        parts: list[str] = []
        for block in content:
            if isinstance(block, TextContent):
                parts.append(block.text)
                continue

            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)

        return "\n".join(part for part in parts if part).strip()

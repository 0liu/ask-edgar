"""The MCP server: in-process through the SDK client, and once over real stdio."""

import asyncio
import os
from typing import Any

import psycopg
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from psycopg.rows import TupleRow

from ask_edgar import mcp_server

pytestmark = pytest.mark.usefixtures("analytics_conn")


def call(name: str, arguments: dict[str, Any] | None = None) -> Any:
    async def go() -> Any:
        async with Client(mcp_server.server) as client:
            return await client.call_tool(name, arguments or {})

    return asyncio.run(go())


def test_lists_the_three_tools() -> None:
    async def go() -> list[str]:
        async with Client(mcp_server.server) as client:
            return sorted(tool.name for tool in (await client.list_tools()).tools)

    assert asyncio.run(go()) == ["get_metric", "get_schema", "run_query"]


def test_get_schema() -> None:
    result = call("get_schema")
    assert result.is_error is False
    assert "CREATE TABLE analytics.fct_financials_wide" in result.content[0].text


def test_run_query_goes_through_the_guard() -> None:
    ok = call(
        "run_query",
        {"sql": "select revenue from analytics.fct_financials_wide where cik = 1652044"},
    )
    assert ok.is_error is False
    assert ok.structured_content["rows"] == [[350018000000]]
    assert ok.structured_content["truncated"] is False

    rejected = call("run_query", {"sql": "select * from raw.sub"})
    assert rejected.is_error is True
    assert "query rejected: relation raw.sub" in rejected.content[0].text

    failed = call("run_query", {"sql": "select nope from analytics.dim_company"})
    assert failed.is_error is True
    assert "query failed" in failed.content[0].text


def test_get_metric_returns_the_figure_with_provenance() -> None:
    result = call("get_metric", {"company": "Tesla", "metric": "total_assets"})
    assert result.is_error is False
    payload = result.structured_content
    row = dict(zip(payload["columns"], payload["rows"][0], strict=True))
    assert row["company_name"] == "TESLA, INC."
    assert row["value"] == 122070000000
    assert row["uom"] == "USD"
    assert row["source_tag"] == "Assets"
    assert row["adsh"].startswith("0001628280-25")

    empty = call("get_metric", {"company": "Tes'la", "metric": "revenue", "fiscal_year": 1999})
    assert empty.is_error is False
    assert empty.structured_content["row_count"] == 0
    assert "No row" in empty.structured_content["note"]


def test_stdio_entry_point(analytics_conn: psycopg.Connection[TupleRow]) -> None:
    """The console script speaks MCP on stdout and keeps its logging on stderr."""
    params = StdioServerParameters(
        command="uv",
        args=["run", "ask-edgar-mcp"],
        env={"DATABASE_URL": os.environ["DATABASE_URL"]},
    )

    async def go() -> Any:
        async with Client(params) as client:
            return await client.call_tool("run_query", {"sql": "select 1 as one"})

    result = asyncio.run(go())
    assert result.is_error is False
    assert result.structured_content["rows"] == [[1]]

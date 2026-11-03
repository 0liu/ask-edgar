"""MCP server (SPEC §6): ``get_schema``, ``run_query`` and ``get_metric`` over stdio.

Registered in ``.mcp.json`` as ``uv run --env-file .env ask-edgar-mcp``, so Claude Code
and Claude Desktop can query the warehouse directly. ``run_query`` goes through the same
guard as the agent. Nothing here may write to stdout: that is the protocol channel.
"""

import logging
import sys
from typing import Any

import psycopg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from psycopg import sql as pgsql

from ask_edgar import guard, tools

log = logging.getLogger(__name__)

server = MCPServer(
    name="ask-edgar",
    instructions=(
        "Annual 10-K financial statement figures for US public companies, from SEC filings "
        "made January to March 2025 (mostly fiscal year 2024). Call get_schema once, then "
        "query the analytics.* tables with run_query, or use get_metric for one figure."
    ),
)


@server.tool()
def get_schema() -> str:
    """The warehouse schema: DDL of the analytics tables, the metric list with definitions,
    and sample rows of analytics.fct_financials_wide."""
    return tools.get_schema()


@server.tool()
def run_query(sql: str) -> dict[str, Any]:
    """Run one read-only SELECT against the analytics schema. Returns {columns, rows,
    row_count, truncated}; at most 50 rows come back. Tables must be qualified with
    `analytics.`; only SELECT is allowed. On failure the error text says what to fix."""
    try:
        return tools.run_query(sql).as_dict()
    except guard.GuardError as exc:
        raise ToolError(f"query rejected: {exc}") from exc
    except psycopg.Error as exc:
        raise ToolError(f"query failed: {exc.diag.message_primary or exc}") from exc


@server.tool()
def get_metric(company: str, metric: str, fiscal_year: int = 2024) -> dict[str, Any]:
    """One metric for one company and fiscal year, with the source tag and filing.

    company: start of the filer's legal name, case-insensitive ("Tesla", "Alphabet",
    "Amazon"). metric: a metric name from get_schema ("revenue", "net_income",
    "total_assets", ...). Several rows come back when the name prefix matches several
    filers; no rows means the company, metric or year is not in the data."""
    query = pgsql.SQL(
        "select company_name, cik, fiscal_year, metric, value, uom, source_tag, adsh, form, "
        "period_end from {} where company_name ilike {} and metric = {} and fiscal_year = {} "
        "order by company_name"
    ).format(
        pgsql.Identifier(tools.SCHEMA, "fct_financials"),
        pgsql.Literal(f"{company.strip()}%"),
        pgsql.Literal(metric.strip().lower()),
        pgsql.Literal(fiscal_year),
    )
    try:
        result = tools.run_query(query.as_string()).as_dict()
    except (guard.GuardError, psycopg.Error) as exc:
        raise ToolError(f"lookup failed: {exc}") from exc
    if result["row_count"] == 0:
        result["note"] = (
            "No row. Check the company name against analytics.dim_company "
            "(select name from analytics.dim_company where name ilike '%...%') and the "
            "metric name against get_schema; most rows are fiscal_year 2024."
        )
    return result


def main() -> None:
    """Entry point for ``ask-edgar-mcp``: stdio transport, logs to stderr."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(name)s: %(message)s")
    log.info("ask-edgar MCP server starting on stdio")
    server.run("stdio")

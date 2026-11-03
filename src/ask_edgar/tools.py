"""The two tools the agent exposes: ``get_schema`` and ``run_query`` (SPEC §5.2).

Plain functions, so the REST API and the MCP server share them with the agent.
``run_query`` is the only place that executes model-written SQL: it goes through
``guard.validate`` and ``db.read_only_cursor``, and never returns more than
``MAX_ROWS`` rows.
"""

import datetime as dt
import functools
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import NamedTuple

import psycopg
from psycopg import sql as pgsql
from psycopg.rows import TupleRow

from ask_edgar import db, guard

MAX_ROWS = guard.MAX_ROWS
SCHEMA = guard.ALLOWED_SCHEMA
TABLES = ("dim_company", "fct_financials", "fct_financials_wide")

# Companies shown as sample rows: large, well known, with a 10-K in 2025q1 and part of
# the CI fixture, so the schema text is identical against the full quarter and the
# fixture (the eval cassettes are keyed on the request, which includes it).
SAMPLE_CIKS = (1652044, 1018724, 1318605)  # Alphabet, Amazon, Tesla
SAMPLE_FISCAL_YEAR = 2024

TABLE_NOTES = {
    "dim_company": "one row per company (cik), from its most recent filing in the quarter",
    "fct_financials": "one row per (cik, fiscal_year, metric): the annual 10-K figure "
    "and its source",
    "fct_financials_wide": "one row per (cik, fiscal_year), one column per metric; "
    "NULL = nothing reported under a mapped tag",
}
COLUMN_NOTES = {
    "cik": "SEC Central Index Key, the company id; the same across tables",
    "name": "legal filer name in upper case, e.g. ALPHABET INC., AMAZON COM INC",
    "company_name": "legal filer name in upper case; match with ILIKE",
    "sic": "Standard Industrial Classification code",
    "sic_description": "industry name for sic; NULL when the filer has none",
    "state_of_incorporation": "two-letter state or country code",
    "fiscal_year_end": "month and day the fiscal year ends, MMDD (1231 = 31 Dec)",
    "latest_adsh": "accession number of the company's most recent filing in the quarter",
    "latest_form": "form type of that filing (10-K, 10-Q, 8-K, ...)",
    "latest_filed": "date that filing was made",
    "adsh": "accession number of the 10-K the figure comes from",
    "form": "10-K or 10-K/A",
    "fiscal_year": "fiscal year of the figure; mostly 2024, some 2023",
    "fiscal_period": "always FY",
    "period_end": "balance-sheet date the fiscal year ends on",
    "metric": "metric name; the columns of fct_financials_wide (list below)",
    "value": "the reported figure; USD unless uom says otherwise",
    "uom": "unit: USD, shares, USD/shares, or a foreign currency code",
    "source_tag": "XBRL tag the figure was taken from",
    "qtrs": "0 for balance-sheet figures, 4 for full-year flows",
}

type JsonValue = str | int | float | bool | None


class SchemaError(RuntimeError):
    """The analytics schema is missing; ``make dbt`` has not been run."""


class Metric(NamedTuple):
    metric: str
    kind: str
    description: str
    tags: str


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[JsonValue]]
    row_count: int
    truncated: bool
    sql: str  # the guarded statement, with the row cap applied, for display

    def as_dict(self) -> dict[str, object]:
        """The tool output contract: ``{columns, rows, row_count, truncated}``."""
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False)


def jsonable(value: object) -> JsonValue:
    """Convert a Postgres value to something JSON (and the model) can read."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    return str(value)


def run_query(sql: str, conn: psycopg.Connection[TupleRow] | None = None) -> QueryResult:
    """Validate ``sql``, run it read-only, and return at most ``MAX_ROWS`` rows.

    Raises ``guard.GuardError`` for SQL the guard refuses and ``psycopg.Error`` for
    SQL Postgres refuses; both messages are meant to go back to the model.
    """
    shown = guard.validate(sql)
    # One extra row tells truncation apart from a result that is exactly MAX_ROWS long.
    probe = guard.validate(sql, max_rows=MAX_ROWS + 1)
    with db.read_only_cursor(conn) as cur:
        cur.execute(probe.encode("utf-8"))
        columns = [column.name for column in cur.description or []]
        fetched = cur.fetchall()
    rows = [[jsonable(value) for value in row] for row in fetched[:MAX_ROWS]]
    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=len(fetched) > MAX_ROWS,
        sql=shown,
    )


@functools.cache
def get_schema() -> str:
    """The schema text the agent sees, built once per process from the database."""
    return build_schema_text()


def build_schema_text(conn: psycopg.Connection[TupleRow] | None = None) -> str:
    """DDL of the three marts, the metric list with definitions, and sample rows."""
    with db.read_only_cursor(conn) as cur:
        columns = _columns(cur)
        metrics = _metrics(cur)
        sample = _sample_rows(cur)
    descriptions = {m.metric: m.description for m in metrics}
    parts = [
        f"-- Schema {SCHEMA} (Postgres): three relations. Qualify every table with the "
        f"schema name, e.g. {SCHEMA}.fct_financials_wide."
    ]
    parts.extend(_ddl(table, columns[table], descriptions) for table in TABLES)
    parts.append(_metric_list(metrics))
    parts.append(_sample_table(*sample))
    return "\n\n".join(parts)


def _columns(cur: psycopg.Cursor[TupleRow]) -> dict[str, list[tuple[str, str]]]:
    cur.execute(
        """
        select table_name, column_name, data_type
        from information_schema.columns
        where table_schema = %s and table_name = any(%s)
        order by table_name, ordinal_position
        """,
        (SCHEMA, list(TABLES)),
    )
    columns: dict[str, list[tuple[str, str]]] = {table: [] for table in TABLES}
    for table, column, data_type in cur.fetchall():
        columns[table].append((column, data_type))
    missing = [table for table in TABLES if not columns[table]]
    if missing:
        raise SchemaError(f"{SCHEMA}.{missing[0]} not found; run `make dbt` first")
    return columns


def _metrics(cur: psycopg.Cursor[TupleRow]) -> list[Metric]:
    cur.execute(
        pgsql.SQL(
            "select metric, kind, description, string_agg(tag, ', ' order by priority) "
            "from {} group by metric, kind, description order by metric"
        ).format(pgsql.Identifier(SCHEMA, "metric_map"))
    )
    return [Metric(*row) for row in cur.fetchall()]


def _sample_rows(cur: psycopg.Cursor[TupleRow]) -> tuple[list[str], list[list[JsonValue]]]:
    cur.execute(
        pgsql.SQL("select * from {} where cik = any(%s) and fiscal_year = %s order by cik").format(
            pgsql.Identifier(SCHEMA, "fct_financials_wide")
        ),
        (list(SAMPLE_CIKS), SAMPLE_FISCAL_YEAR),
    )
    columns = [column.name for column in cur.description or []]
    rows = [[jsonable(value) for value in row] for row in cur.fetchall()]
    return columns, rows


def _ddl(table: str, columns: list[tuple[str, str]], descriptions: dict[str, str]) -> str:
    width = max(len(f"{name} {data_type},") for name, data_type in columns)
    lines = [f"-- {TABLE_NOTES[table]}", f"CREATE TABLE {SCHEMA}.{table} ("]
    for name, data_type in columns:
        declaration = f"{name} {data_type},"
        note = COLUMN_NOTES.get(name) or descriptions.get(name)
        lines.append(f"    {declaration:<{width}}  -- {note}" if note else f"    {declaration}")
    lines[-1] = lines[-1].replace(",", "", 1)
    lines.append(");")
    return "\n".join(lines)


def _metric_list(metrics: list[Metric]) -> str:
    lines = [
        f"-- Metrics: the {len(metrics)} metric columns of fct_financials_wide, also the values "
        "of fct_financials.metric.",
        "-- balance = at fiscal year end; flow = over the fiscal year; per_share = USD per share.",
        "-- Each metric is filled from the first XBRL tag listed that the company reported.",
    ]
    lines.extend(f"{m.metric} ({m.kind}): {m.description} [{m.tags}]" for m in metrics)
    return "\n".join(lines)


def _sample_table(columns: list[str], rows: list[list[JsonValue]]) -> str:
    lines = [
        f"-- Sample rows of {SCHEMA}.fct_financials_wide, fiscal year {SAMPLE_FISCAL_YEAR} "
        "(amounts in USD, EPS in USD per share, share counts in shares):",
        " | ".join(columns),
    ]
    lines.extend(" | ".join("NULL" if v is None else str(v) for v in row) for row in rows)
    return "\n".join(lines)

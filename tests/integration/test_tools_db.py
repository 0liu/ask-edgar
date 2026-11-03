"""``run_query`` and the schema text against the dbt marts (skipped until `make dbt`)."""

import psycopg
import pytest
from psycopg.rows import TupleRow

from ask_edgar import guard, tools


def test_run_query_returns_typed_rows(analytics_conn: psycopg.Connection[TupleRow]) -> None:
    result = tools.run_query(
        "select cik, company_name, revenue, period_end from analytics.fct_financials_wide "
        "where cik = 1652044 and fiscal_year = 2024",
        analytics_conn,
    )
    assert result.columns == ["cik", "company_name", "revenue", "period_end"]
    assert result.rows == [[1652044, "ALPHABET INC.", 350018000000, "2024-12-31"]]
    assert result.row_count == 1
    assert result.truncated is False
    assert result.sql.rstrip().endswith("LIMIT 50")


def test_run_query_caps_and_flags_truncation(analytics_conn: psycopg.Connection[TupleRow]) -> None:
    result = tools.run_query("select cik from analytics.fct_financials", analytics_conn)
    assert result.row_count == tools.MAX_ROWS == len(result.rows)
    assert result.truncated is True


def test_run_query_respects_a_smaller_limit(analytics_conn: psycopg.Connection[TupleRow]) -> None:
    result = tools.run_query(
        "select cik from analytics.fct_financials order by cik limit 3", analytics_conn
    )
    assert result.row_count == 3
    assert result.truncated is False


def test_guard_rejects_before_touching_the_database(
    analytics_conn: psycopg.Connection[TupleRow],
) -> None:
    with pytest.raises(guard.GuardError):
        tools.run_query("select * from raw.sub", analytics_conn)


def test_postgres_errors_propagate_and_leave_the_connection_usable(
    analytics_conn: psycopg.Connection[TupleRow],
) -> None:
    with pytest.raises(psycopg.errors.UndefinedColumn):
        tools.run_query("select no_such_column from analytics.dim_company", analytics_conn)
    assert tools.run_query("select 1 as one", analytics_conn).rows == [[1]]


@pytest.mark.parametrize(
    "sql",
    [
        "select company_name from analytics.fct_financials_wide "
        "where company_name ilike 'ALPHABET INC%' and fiscal_year = 2024",
        "select count(*) filter (where net_income < 0) from analytics.fct_financials_wide",
        "select total_liabilities::numeric / nullif(stockholders_equity, 0) "
        "from analytics.fct_financials_wide where cik = 1318605",
        "select company_name, rank() over (order by total_assets desc nulls last) "
        "from analytics.fct_financials_wide where fiscal_year = 2024 limit 5",
        "select string_agg(name, ', ' order by name) from analytics.dim_company where sic = 7370",
        "select extract(year from period_end), round(avg(revenue) / 1e9, 1) "
        "from analytics.fct_financials_wide group by 1",
        "select * from analytics.dim_company where latest_filed > date '2025-01-01' "
        "- interval '1 day' limit 1",
        "with ranked as (select cik, revenue, row_number() over (order by revenue desc) as rn "
        "from analytics.fct_financials_wide where fiscal_year = 2024) "
        "select * from ranked where rn <= 3",
    ],
)
def test_regenerated_sql_runs_on_postgres(
    analytics_conn: psycopg.Connection[TupleRow], sql: str
) -> None:
    tools.run_query(sql, analytics_conn)


def test_schema_text_describes_the_marts(analytics_conn: psycopg.Connection[TupleRow]) -> None:
    text = tools.build_schema_text(analytics_conn)
    for table in tools.TABLES:
        assert f"CREATE TABLE analytics.{table} (" in text
    assert "revenue (flow): Total revenues" in text
    assert "ALPHABET INC." in text
    assert "metric_map" not in text.split("-- Metrics")[0]  # the seed is not a query target
    assert text == tools.build_schema_text(analytics_conn)  # deterministic

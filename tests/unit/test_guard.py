"""The SQL guard: what gets through, what is refused, and how the row cap is applied."""

import pytest

from ask_edgar import guard
from ask_edgar.guard import GuardError, validate

ACCEPT = [
    "select revenue from analytics.fct_financials_wide where company_name ilike 'ALPHABET INC%'",
    "SELECT company_name, net_income FROM analytics.fct_financials_wide "
    "WHERE fiscal_year = 2024 ORDER BY net_income DESC NULLS LAST LIMIT 5",
    "with top as (select cik from analytics.fct_financials_wide where fiscal_year = 2024 "
    "order by total_assets desc limit 5) "
    "select c.name from analytics.dim_company c join top using (cik)",
    "select cik from analytics.dim_company union all select cik from analytics.fct_financials",
    "select count(*) filter (where net_income < 0) as losses, "
    "sum(value)::numeric / nullif(count(*), 0) as avg_value "
    "from analytics.fct_financials where metric = 'net_income'",
    "select company_name, row_number() over (order by revenue desc) as rank "
    "from analytics.fct_financials_wide where fiscal_year = 2024",
    "select * from (select cik, revenue from analytics.fct_financials_wide) sub where revenue > 0",
    "select sic_description, count(*) from analytics.dim_company group by 1 order by 2 desc",
    "select name from ANALYTICS.dim_company where state_of_incorporation = 'DE';",
    "select cik -- the id\nfrom analytics.dim_company /* comment */",
    "select c.name, w.total_liabilities / nullif(w.stockholders_equity, 0) as ratio "
    "from analytics.fct_financials_wide w join analytics.dim_company c on c.cik = w.cik "
    "where c.name ilike 'TESLA%' and w.fiscal_year = 2024",
    "select extract(year from period_end) as y, max(revenue) from analytics.fct_financials_wide "
    "group by 1",
    "select * from analytics.dim_company c, lateral (select 1 as one) s",
]

REJECT = [
    ("select 1; select 2", "one statement"),
    ("insert into analytics.dim_company (cik) values (1)", "INSERT"),
    ("update analytics.dim_company set name = 'x'", "UPDATE"),
    ("delete from analytics.dim_company", "DELETE"),
    ("drop table analytics.dim_company", "DROP"),
    ("create table analytics.t as select 1", "CREATE"),
    ("with w as (insert into analytics.t values (1) returning *) select * from w", "INSERT"),
    ("select * into t from analytics.dim_company", "SELECT INTO"),
    ("select * from fct_financials_wide", "analytics.fct_financials_wide"),
    ("select * from raw.num", "relation raw.num"),
    ("select * from pg_stat_activity", "relation pg_stat_activity"),
    ("select * from information_schema.tables", "relation information_schema.tables"),
    ("select * from edgar.analytics.dim_company", "database prefix"),
    ("select * from generate_series(1, 10)", "table functions"),
    ("select pg_sleep(10)", "pg_sleep()"),
    ("select pg_read_file('/etc/passwd')", "pg_read_file()"),
    ("select * from analytics.dim_company for update", "FOR UPDATE"),
    ("explain select 1", "EXPLAIN"),
    ("set search_path to raw", "only SELECT"),
    ("show search_path", "only SELECT"),
    ("", "empty"),
    ("select from where", "could not be parsed"),
]


@pytest.mark.parametrize("sql", ACCEPT)
def test_accepts_read_only_analytics_queries(sql: str) -> None:
    out = validate(sql)
    assert out.upper().startswith(("SELECT", "WITH"))
    assert "LIMIT" in out


@pytest.mark.parametrize(("sql", "reason"), REJECT)
def test_rejects_with_a_reason_the_model_can_act_on(sql: str, reason: str) -> None:
    with pytest.raises(GuardError, match=reason.replace("(", r"\(").replace(")", r"\)")):
        validate(sql)


def test_keeps_a_limit_within_the_cap() -> None:
    out = validate("select cik from analytics.dim_company limit 10")
    assert "LIMIT 10" in out
    assert "LIMIT 50" not in out


def test_lowers_a_limit_above_the_cap() -> None:
    out = validate("select cik from analytics.dim_company limit 5000")
    assert "LIMIT 50" in out
    assert "5000" not in out


def test_fetch_first_counts_as_a_limit() -> None:
    assert "LIMIT 50" not in validate(
        "select cik from analytics.dim_company fetch first 10 rows only"
    )
    assert "LIMIT 50" in validate("select cik from analytics.dim_company fetch first 500 rows only")


def test_offset_survives_and_order_by_is_kept() -> None:
    out = " ".join(validate("select cik from analytics.dim_company order by cik offset 5").split())
    assert out == "SELECT cik FROM analytics.dim_company ORDER BY cik LIMIT 50 OFFSET 5"


def test_max_rows_is_configurable() -> None:
    assert "LIMIT 51" in validate("select cik from analytics.dim_company", max_rows=51)
    assert guard.MAX_ROWS == 50


def test_cte_names_do_not_whitelist_their_contents() -> None:
    with pytest.raises(GuardError, match=r"raw\.sub"):
        validate("with s as (select * from raw.sub) select * from s")


def test_limit_applies_to_the_whole_set_operation() -> None:
    out = validate(
        "select cik from analytics.dim_company union select cik from analytics.fct_financials"
    )
    assert out.rstrip().endswith("LIMIT 50")
    assert out.count("LIMIT") == 1

"""Export the four raw tables for 30 well-known filers to ``tests/fixtures/`` (SPEC §8).

Run after ``ask-edgar load`` with the full quarter::

    uv run --env-file .env python scripts/make_fixture.py [--all-segments]

The files keep the SEC layout (tab-separated, header row, CSV-style quoting, YYYYMMDD
dates, 0/1 flags, empty for NULL) so ``ask-edgar load --fixture`` reads them with the same
code as the real files. ``num`` keeps only consolidated facts (no segment axis, no
co-registrant) by default: the marts use nothing else, and it keeps the fixture small.
``tag`` keeps the definitions the exported ``num`` and ``pre`` rows reference.
"""

import argparse
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import TupleRow

from ask_edgar import db
from ask_edgar.etl.load_raw import DEFAULT_SCHEMA, FIXTURE_DIR, TABLES, RawTable

# The PLAN's list. Filers with only a 10-Q in 2025q1 (Apple, Microsoft, ...) still get a
# dim_company row, which is what the "not in the loaded quarter" answers rely on.
FIXTURE_CIKS: dict[str, int] = {
    "Apple": 320193,
    "Microsoft": 789019,
    "Amazon": 1018724,
    "Alphabet": 1652044,
    "Meta": 1326801,
    "Tesla": 1318605,
    "Nvidia": 1045810,
    "JPMorgan": 19617,
    "Berkshire": 1067983,
    "Exxon": 34088,
    "Walmart": 104169,
    "Johnson & Johnson": 200406,
    "Procter & Gamble": 80424,
    "Coca-Cola": 21344,
    "Pfizer": 78003,
    "Intel": 50863,
    "Cisco": 858877,
    "Oracle": 1341439,
    "Netflix": 1065280,
    "Costco": 909832,
    "Home Depot": 354950,
    "Boeing": 12927,
    "Caterpillar": 18230,
    "Ford": 37996,
    "General Motors": 1467858,
    "Delta": 27904,
    "Starbucks": 829224,
    "Nike": 320187,
    "McDonald's": 63908,
    "Disney": 1744489,
}

SUBSET_ADSH = sql.SQL("select adsh from {} where cik = any(%(ciks)s)").format(
    sql.Identifier(DEFAULT_SCHEMA, "sub")
)
CONSOLIDATED = sql.SQL(" and segments is null and coreg is null")


def column_types(cur: psycopg.Cursor[TupleRow]) -> dict[tuple[str, str], str]:
    cur.execute(
        "select table_name, column_name, data_type from information_schema.columns "
        "where table_schema = %s",
        (DEFAULT_SCHEMA,),
    )
    return {(table, column): data_type for table, column, data_type in cur.fetchall()}


def column_expr(name: str, data_type: str) -> sql.Composable:
    """Render a column the way the SEC files spell it."""
    ident = sql.Identifier(name)
    if data_type == "date":
        return sql.SQL("to_char({}, 'YYYYMMDD') as {}").format(ident, ident)
    if data_type.startswith("timestamp"):
        return sql.SQL("to_char({}, 'YYYY-MM-DD HH24:MI:SS.MS') as {}").format(ident, ident)
    if data_type == "boolean":
        return sql.SQL("{}::int as {}").format(ident, ident)
    return ident


def table_query(
    table: RawTable, types: dict[tuple[str, str], str], all_segments: bool
) -> sql.Composed:
    columns = sql.SQL(", ").join(column_expr(c, types[table.name, c]) for c in table.columns)
    source = sql.Identifier(DEFAULT_SCHEMA, table.name)
    num_filter = sql.SQL("") if all_segments else CONSOLIDATED
    if table.name == "sub":
        where = sql.SQL("cik = any(%(ciks)s)")
        order = sql.SQL("adsh")
    elif table.name == "num":
        where = sql.SQL("adsh in ({})").format(SUBSET_ADSH) + num_filter
        order = sql.SQL("adsh, tag, version, ddate, qtrs, uom, segments, coreg")
    elif table.name == "pre":
        where = sql.SQL("adsh in ({})").format(SUBSET_ADSH)
        order = sql.SQL("adsh, report, line")
    else:
        where = sql.SQL(
            "(tag, version) in (select tag, version from {} where adsh in ({}){} "
            "union select tag, version from {} where adsh in ({}))"
        ).format(
            sql.Identifier(DEFAULT_SCHEMA, "num"),
            SUBSET_ADSH,
            num_filter,
            sql.Identifier(DEFAULT_SCHEMA, "pre"),
            SUBSET_ADSH,
        )
        order = sql.SQL("tag, version")
    return sql.SQL("select {} from {} where {} order by {}").format(columns, source, where, order)


def export(conn: psycopg.Connection[TupleRow], out_dir: Path, *, all_segments: bool) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    ciks = list(FIXTURE_CIKS.values())
    total = 0
    with conn.cursor() as cur:
        types = column_types(cur)
        for table in TABLES:
            query = table_query(table, types, all_segments)
            copy_stmt = sql.SQL(
                "copy ({}) to stdout (format csv, delimiter E'\\t', header true, quote '\"')"
            ).format(query)
            path = out_dir / table.filename
            rows = -1  # the header
            with path.open("wb") as fh, cur.copy(copy_stmt, {"ciks": ciks}) as copy:
                for chunk in copy:
                    fh.write(chunk)
                    rows += bytes(chunk).count(b"\n")
            size = path.stat().st_size
            total += size
            print(f"{table.filename:<8} {rows:>7,} rows {size / 1e6:>6.2f} MB")
    print(f"total    {total / 1e6:>6.2f} MB in {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=FIXTURE_DIR)
    parser.add_argument(
        "--all-segments", action="store_true", help="keep segment and co-registrant facts too"
    )
    args = parser.parse_args()
    with db.connect() as conn:
        export(conn, args.out, all_segments=args.all_segments)


if __name__ == "__main__":
    main()

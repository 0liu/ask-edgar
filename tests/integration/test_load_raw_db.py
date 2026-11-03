import csv
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import TupleRow

from ask_edgar import db
from ask_edgar.etl import load_raw

SCHEMA = "raw_test"
SCHEMA_SQL = Path(__file__).resolve().parents[2] / "db" / "raw_schema.sql"
ADSH = "0000320193-24-000123"


def _write(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    """Write rows the way the SEC does: tab-delimited, fields with tabs or quotes wrapped."""
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            unknown = set(row) - set(columns)
            assert not unknown, unknown
            writer.writerow([row.get(c, "") for c in columns])


def write_fixture(data_dir: Path) -> None:
    sub, tag, num, pre = load_raw.TABLES
    _write(
        data_dir / sub.filename,
        sub.columns,
        [
            {
                "adsh": ADSH, "cik": "320193", "name": "APPLE INC", "sic": "3571",
                "countryba": "US", "wksi": "1", "fye": "0928", "form": "10-K",
                "period": "20240928", "fy": "2024", "fp": "FY", "filed": "20241101",
                "accepted": "2024-11-01 06:01:36.0", "prevrpt": "0", "detail": "1",
                "instance": "aapl-20240928_htm.xml", "nciks": "1",
            },
            {
                # empty sic/period/fy, and a country code pandas would otherwise read as NaN
                "adsh": "0000000002-25-000001", "cik": "2", "name": "NAMIBIA TEST CO",
                "countryba": "NA", "wksi": "0", "form": "10-Q", "filed": "20250115",
                "accepted": "2025-01-15 10:00:00.0", "prevrpt": "0", "detail": "0",
                "instance": "x.xml", "nciks": "1",
            },
        ],
    )  # fmt: skip
    _write(
        data_dir / tag.filename,
        tag.columns,
        [
            {"tag": "Revenues", "version": "us-gaap/2024", "custom": "0", "abstract": "0",
             "datatype": "monetary", "iord": "D", "crdr": "C", "tlabel": "Revenues",
             "doc": 'Amount of revenue, "net"'},
            {"tag": "CustomTag", "version": ADSH, "custom": "1", "abstract": "0"},
        ],
    )  # fmt: skip
    _write(
        data_dir / num.filename,
        num.columns,
        [
            {"adsh": ADSH, "tag": "Revenues", "version": "us-gaap/2024", "ddate": "20240928",
             "qtrs": "4", "uom": "USD", "value": "391035000000.0000"},
            {"adsh": ADSH, "tag": "Revenues", "version": "us-gaap/2024", "ddate": "20240928",
             "qtrs": "4", "uom": "USD", "value": "201183000000.0000",
             "segments": "InvestmentIdentifier=GPI Acquisition Co., Inc.\tRevolver;"},
            {"adsh": ADSH, "tag": "CustomTag", "version": ADSH, "ddate": "20240928",
             "qtrs": "0", "uom": "USD", "coreg": "AppleSub", "footnote": 'see note "A", below'},
        ],
    )  # fmt: skip
    _write(
        data_dir / pre.filename,
        pre.columns,
        [
            {"adsh": ADSH, "report": "2", "line": "1", "stmt": "IS", "inpth": "0", "rfile": "H",
             "tag": "Revenues", "version": "us-gaap/2024", "plabel": 'Net sales, "total"',
             "negating": "0"},
            {"adsh": ADSH, "report": "2", "line": "2", "stmt": "IS", "inpth": "1", "rfile": "H",
             "tag": "CustomTag", "version": ADSH, "plabel": "Custom line", "negating": "1"},
        ],
    )  # fmt: skip


@pytest.fixture
def scratch_schema(db_url: str) -> Iterator[psycopg.Connection[TupleRow]]:
    with db.connect(db_url) as conn:
        yield conn
        conn.rollback()
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.commit()


def test_load_raw_types_nulls_and_idempotence(
    scratch_schema: psycopg.Connection[TupleRow], tmp_path: Path
) -> None:
    conn = scratch_schema
    write_fixture(tmp_path)

    counts = load_raw.load_raw(conn, tmp_path, schema=SCHEMA, schema_sql=SCHEMA_SQL)
    assert counts == {"sub": 2, "tag": 2, "num": 3, "pre": 2}

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT cik, sic, countryba, period, filed, accepted, wksi, fy "
            f"FROM {SCHEMA}.sub ORDER BY adsh"
        )
        namibia, apple = cur.fetchall()
        assert apple == (
            320193, 3571, "US", date(2024, 9, 28), date(2024, 11, 1),
            datetime(2024, 11, 1, 6, 1, 36), True, 2024,
        )  # fmt: skip
        assert namibia[1] is None  # empty sic -> NULL
        assert namibia[2] == "NA"  # not a missing value
        assert namibia[3] is None and namibia[7] is None

        cur.execute(f"SELECT tag, value, segments, coreg, footnote FROM {SCHEMA}.num ORDER BY 1, 3")
        assert cur.fetchall() == [
            ("CustomTag", None, None, "AppleSub", 'see note "A", below'),
            (
                "Revenues",
                Decimal("201183000000.0000"),
                "InvestmentIdentifier=GPI Acquisition Co., Inc.\tRevolver;",
                None,
                None,
            ),
            ("Revenues", Decimal("391035000000.0000"), None, None, None),
        ]

        cur.execute(f"SELECT line, inpth, plabel, negating FROM {SCHEMA}.pre ORDER BY line")
        assert cur.fetchall() == [
            (1, False, 'Net sales, "total"', False),
            (2, True, "Custom line", True),
        ]

        cur.execute(f"SELECT doc FROM {SCHEMA}.tag WHERE tag = 'Revenues'")
        assert cur.fetchone() == ('Amount of revenue, "net"',)

    # second load truncates first: same counts, no duplicates
    assert load_raw.load_raw(conn, tmp_path, schema=SCHEMA, schema_sql=SCHEMA_SQL) == counts


def test_load_raw_reports_missing_files_and_schema(
    scratch_schema: psycopg.Connection[TupleRow], tmp_path: Path
) -> None:
    conn = scratch_schema
    with pytest.raises(load_raw.LoadError, match="missing source files"):
        load_raw.load_raw(conn, tmp_path, schema=SCHEMA, schema_sql=SCHEMA_SQL)
    write_fixture(tmp_path)
    with pytest.raises(load_raw.LoadError, match="not found"):
        load_raw.load_raw(conn, tmp_path, schema=SCHEMA, schema_sql=tmp_path / "nope.sql")

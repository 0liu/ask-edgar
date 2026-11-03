"""Load the four SEC FSDS tables into Postgres with ``COPY``.

The files are tab-separated with CSV-style quoting: a field holding a tab, comma or quote
is wrapped in double quotes with inner quotes doubled (2025q1 has 787 such fields, 30 of
them with a tab inside). pandas reads each file in chunks of ``CHUNKSIZE`` rows as plain
strings with NA inference off ("NA" is Namibia, not a missing value), re-encodes each
chunk as CSV and streams it to ``COPY ... FROM STDIN (FORMAT csv)``. Empty fields become
NULL. Postgres parses ``YYYYMMDD`` dates and ``NUMERIC`` values itself, so numbers never
pass through a float. Memory stays flat regardless of file size.
"""

import csv
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import psycopg
from psycopg import sql
from psycopg.rows import TupleRow

log = logging.getLogger(__name__)

CHUNKSIZE = 500_000
DEFAULT_SCHEMA = "raw"
DEFAULT_SCHEMA_SQL = Path("db/raw_schema.sql")
DEFAULT_DATA_DIR = Path("data/raw/2025q1")
FIXTURE_DIR = Path("tests/fixtures")
SEC_ZIP_URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/2025q1.zip"


class LoadError(RuntimeError):
    """A source file is missing or does not look like the SEC data set."""


@dataclass(frozen=True)
class RawTable:
    name: str
    columns: tuple[str, ...]

    @property
    def filename(self) -> str:
        return f"{self.name}.txt"


# Column order is the SEC file order (readme.htm, 2025q1). The loader checks the
# header of each file against it, so a changed layout fails loudly instead of
# loading shifted columns.
TABLES: tuple[RawTable, ...] = (
    RawTable(
        "sub",
        (
            "adsh", "cik", "name", "sic", "countryba", "stprba", "cityba", "zipba",
            "bas1", "bas2", "baph", "countryma", "stprma", "cityma", "zipma", "mas1",
            "mas2", "countryinc", "stprinc", "ein", "former", "changed", "afs", "wksi",
            "fye", "form", "period", "fy", "fp", "filed", "accepted", "prevrpt",
            "detail", "instance", "nciks", "aciks",
        ),
    ),
    RawTable(
        "tag",
        ("tag", "version", "custom", "abstract", "datatype", "iord", "crdr", "tlabel", "doc"),
    ),
    RawTable(
        "num",
        (
            "adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg",
            "value", "footnote",
        ),
    ),
    RawTable(
        "pre",
        (
            "adsh", "report", "line", "stmt", "inpth", "rfile", "tag", "version",
            "plabel", "negating",
        ),
    ),
)  # fmt: skip


def read_header(path: Path) -> tuple[str, ...]:
    with path.open(encoding="utf-8") as fh:
        return tuple(fh.readline().rstrip("\r\n").split("\t"))


def read_chunks(path: Path, table: RawTable, chunksize: int = CHUNKSIZE) -> Iterator[pd.DataFrame]:
    """Yield ``path`` as string DataFrames, failing if its header is not the SEC layout."""
    if not path.is_file():
        raise LoadError(f"{path} not found. Run `make data` (expects {SEC_ZIP_URL}).")
    header = read_header(path)
    if header != table.columns:
        raise LoadError(
            f"{path}: unexpected columns.\n  got:      {header}\n  expected: {table.columns}"
        )
    try:
        with pd.read_csv(
            path,
            sep="\t",
            quoting=csv.QUOTE_MINIMAL,
            quotechar='"',
            doublequote=True,
            dtype=str,
            keep_default_na=False,
            chunksize=chunksize,
            encoding="utf-8",
        ) as reader:
            yield from reader
    except pd.errors.ParserError as exc:
        raise LoadError(f"{path}: {exc}") from exc


def chunk_to_csv(chunk: pd.DataFrame) -> str:
    """CSV text for ``COPY ... (FORMAT csv)``: empty fields unquoted, so they load as NULL."""
    return chunk.to_csv(index=False, header=False, lineterminator="\n")


def apply_schema(conn: psycopg.Connection[TupleRow], schema: str, schema_sql: Path) -> None:
    """Create ``schema`` and run the DDL file inside it (the file names no schema)."""
    if not schema_sql.is_file():
        raise LoadError(f"{schema_sql} not found; run from the repository root.")
    with conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        cur.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
        cur.execute(schema_sql.read_text(encoding="utf-8").encode("utf-8"))


def load_raw(
    conn: psycopg.Connection[TupleRow],
    data_dir: Path,
    *,
    schema: str = DEFAULT_SCHEMA,
    schema_sql: Path = DEFAULT_SCHEMA_SQL,
    chunksize: int = CHUNKSIZE,
) -> dict[str, int]:
    """Create the tables if needed, truncate them, COPY all four files; return row counts.

    Everything runs in one transaction, so a failure leaves the previous contents
    in place. Loading the same directory twice gives the same result.
    """
    missing = [str(data_dir / t.filename) for t in TABLES if not (data_dir / t.filename).is_file()]
    if missing:
        raise LoadError(
            f"missing source files: {', '.join(missing)}. Run `make data` (expects {SEC_ZIP_URL})."
        )
    counts: dict[str, int] = {}
    with conn.transaction():
        apply_schema(conn, schema, schema_sql)
        with conn.cursor() as cur:
            for table in TABLES:
                target = sql.Identifier(schema, table.name)
                columns = sql.SQL(", ").join(sql.Identifier(c) for c in table.columns)
                cur.execute(sql.SQL("TRUNCATE {}").format(target))
                started = time.perf_counter()
                written = 0
                copy_stmt = sql.SQL("COPY {} ({}) FROM STDIN (FORMAT csv)").format(target, columns)
                with cur.copy(copy_stmt) as copy:
                    for chunk in read_chunks(data_dir / table.filename, table, chunksize):
                        copy.write(chunk_to_csv(chunk))
                        written += len(chunk)
                        log.info("%s: %s rows copied", table.name, f"{written:,}")
                cur.execute(sql.SQL("SELECT count(*) FROM {}").format(target))
                row = cur.fetchone()
                counts[table.name] = int(row[0]) if row else 0
                log.info(
                    "%s: %s rows in %.1fs",
                    table.name,
                    f"{counts[table.name]:,}",
                    time.perf_counter() - started,
                )
    return counts

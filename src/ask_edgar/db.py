"""Database access: a connection factory and a read-only cursor.

Everything that runs SQL on behalf of the model goes through ``read_only_cursor`` so
that ``READ ONLY`` and ``statement_timeout`` are enforced by the server, not by
convention.
"""

import os
from collections.abc import Generator
from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg.rows import TupleRow

DEFAULT_STATEMENT_TIMEOUT_MS = 5_000


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


def database_url() -> str:
    """Return ``DATABASE_URL`` from the environment, or fail with a clear message."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise ConfigError(
            "DATABASE_URL is not set. Copy .env.example to .env (make exports it), or "
            "export it yourself, e.g. postgresql://edgar:edgar@localhost:5432/edgar"
        )
    return url


def connect(url: str | None = None) -> psycopg.Connection[TupleRow]:
    """Open a new connection. The caller owns it; use it as a context manager."""
    return psycopg.connect(url or database_url())


@contextmanager
def read_only_cursor(
    conn: psycopg.Connection[TupleRow] | None = None,
    *,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
) -> Generator[psycopg.Cursor[TupleRow]]:
    """Yield a cursor inside a ``READ ONLY`` transaction with a statement timeout.

    If ``conn`` is omitted, a connection is opened for the duration of the block.
    The transaction is committed on success (a no-op for reads) or rolled back on
    error, so the connection is left clean either way.
    """
    if statement_timeout_ms <= 0:
        raise ValueError("statement_timeout_ms must be positive")
    owned = conn is None
    connection = connect() if conn is None else conn
    try:
        with connection.transaction(), connection.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(
                sql.SQL("SET LOCAL statement_timeout = {}").format(
                    sql.Literal(statement_timeout_ms)
                )
            )
            yield cur
    finally:
        if owned:
            connection.close()

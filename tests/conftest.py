import os
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.rows import TupleRow


@pytest.fixture
def db_url() -> str:
    """Connection string for integration tests; skip when no database is configured."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.skip("DATABASE_URL not set: run `make up` and create .env from .env.example")
    return url


@pytest.fixture
def analytics_conn(db_url: str) -> Iterator[psycopg.Connection[TupleRow]]:
    """A connection to a database where the dbt marts exist; skip otherwise."""
    with psycopg.connect(db_url) as conn:
        row = conn.execute("select to_regclass('analytics.fct_financials_wide')").fetchone()
        if row is None or row[0] is None:
            pytest.skip("analytics schema not built: run `make dbt`")
        yield conn

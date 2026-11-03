import psycopg
import pytest

from ask_edgar import db


def test_select_works(db_url: str) -> None:
    with db.connect(db_url) as conn, db.read_only_cursor(conn) as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)


def test_writes_are_rejected_by_the_server(db_url: str) -> None:
    with (
        db.connect(db_url) as conn,
        pytest.raises(psycopg.errors.ReadOnlySqlTransaction),
        db.read_only_cursor(conn) as cur,
    ):
        cur.execute("CREATE TABLE should_not_exist (x int)")


def test_statement_timeout_is_set(db_url: str) -> None:
    with db.connect(db_url) as conn, db.read_only_cursor(conn, statement_timeout_ms=1234) as cur:
        cur.execute("SHOW statement_timeout")
        assert cur.fetchone() == ("1234ms",)


def test_statement_timeout_fires(db_url: str) -> None:
    with (
        db.connect(db_url) as conn,
        pytest.raises(psycopg.errors.QueryCanceled),
        db.read_only_cursor(conn, statement_timeout_ms=100) as cur,
    ):
        cur.execute("SELECT pg_sleep(2)")


def test_connection_is_usable_after_a_failed_block(db_url: str) -> None:
    with db.connect(db_url) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction), db.read_only_cursor(conn) as cur:
            cur.execute("CREATE TABLE should_not_exist (x int)")
        with db.read_only_cursor(conn) as cur:
            cur.execute("SELECT 2")
            assert cur.fetchone() == (2,)


def test_opens_its_own_connection_when_none_given(
    db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    with db.read_only_cursor() as cur:
        cur.execute("SELECT current_user")
        assert cur.fetchone() == ("edgar",)

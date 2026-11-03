import pytest

from ask_edgar import db


def test_database_url_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(db.ConfigError, match="DATABASE_URL"):
        db.database_url()


def test_database_url_blank_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "   ")
    with pytest.raises(db.ConfigError):
        db.database_url()


def test_database_url_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:1/d")
    assert db.database_url() == "postgresql://u:p@h:1/d"


def test_read_only_cursor_rejects_non_positive_timeout() -> None:
    # Validation happens before any connection is opened.
    with pytest.raises(ValueError, match="positive"), db.read_only_cursor(statement_timeout_ms=0):
        pass

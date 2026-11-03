from pathlib import Path

import pytest
from typer.testing import CliRunner

from ask_edgar.cli import app

runner = CliRunner()


def test_load_is_a_subcommand() -> None:
    result = runner.invoke(app, ["load", "--help"])
    assert result.exit_code == 0, result.output
    assert "--fixture" in result.output
    assert "--data-dir" in result.output


def test_load_fails_cleanly_without_database_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = runner.invoke(app, ["load", "--data-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output

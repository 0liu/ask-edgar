from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from ask_edgar.cli import app

runner = CliRunner()


def test_load_is_a_subcommand_with_its_options() -> None:
    # Inspect the command tree rather than rendered help: Typer switches to rich
    # terminal output (ANSI codes, boxes) when GITHUB_ACTIONS or FORCE_COLOR is set.
    group = typer.main.get_command(app)
    load = group.commands["load"]  # type: ignore[attr-defined]
    option_names = {opt for param in load.params for opt in param.opts}
    assert {"--fixture", "--data-dir", "--schema-sql"} <= option_names


def test_load_help_renders() -> None:
    result = runner.invoke(app, ["load", "--help"])
    assert result.exit_code == 0, result.output


def test_load_fails_cleanly_without_database_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = runner.invoke(app, ["load", "--data-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "DATABASE_URL" in result.output

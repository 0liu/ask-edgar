"""Command-line interface: ``ask-edgar load`` (more commands arrive in later phases)."""

import logging
import time
from pathlib import Path
from typing import Annotated

import psycopg
import typer

from ask_edgar import db
from ask_edgar.etl import load_raw

app = typer.Typer(
    help="Ask questions about SEC 10-K financials in plain English.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


@app.callback()
def main() -> None:
    """Keep subcommand mode while ``load`` is the only command (``ask-edgar load``)."""


@app.command()
def load(
    fixture: Annotated[
        bool,
        typer.Option("--fixture", help="Load the CI fixture subset from tests/fixtures."),
    ] = False,
    data_dir: Annotated[
        Path | None,
        typer.Option(help="Directory holding sub/num/tag/pre .txt (default: data/raw/2025q1)."),
    ] = None,
    schema_sql: Annotated[
        Path, typer.Option(help="DDL for the raw schema.")
    ] = load_raw.DEFAULT_SCHEMA_SQL,
) -> None:
    """Load the raw SEC tables into schema ``raw`` and print row counts."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    source = data_dir or (load_raw.FIXTURE_DIR if fixture else load_raw.DEFAULT_DATA_DIR)
    started = time.perf_counter()
    try:
        with db.connect() as conn:
            counts = load_raw.load_raw(conn, source, schema_sql=schema_sql)
    except (load_raw.LoadError, db.ConfigError, psycopg.Error) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"\nloaded {source} into schema raw in {time.perf_counter() - started:.1f}s")
    typer.echo(f"{'table':<6} {'rows':>12}")
    for name, rows in counts.items():
        typer.echo(f"{name:<6} {rows:>12,}")

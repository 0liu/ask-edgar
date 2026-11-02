# CLAUDE.md

Text-to-SQL agent over SEC Financial Statement Data Sets. Read `SPEC.md` (the contract)
before changing anything.

## Commands

- `make up` / `make down` — Postgres 16 via docker-compose
- `make data` — unzip `data/raw/2025q1.zip`
- `uv run ask-edgar load [--fixture]` — load raw tables
- `make dbt` — `dbt build` (staging → marts → tests)
- `uv run ask-edgar ask "..."` — one question
- `make evals` — replay cassettes, no API key needed; `make evals-live` — call the API and re-record
- `make check` — ruff + mypy --strict + pytest; must be green before every commit
- `make api` — FastAPI on :8000; `make mcp` — stdio MCP server

## Rules

- The SQL guard (`src/ask_edgar/guard.py`) is the only thing standing between the model
  and the database. Never loosen it to make a test pass; fix the prompt or the schema text.
- Ground truth for evals comes from `oracle_sql` executed against the DB, never from
  hard-coded numbers.
- `data/`, `.env`, and `evals/results/*.json` are never committed.
- Commits: `phase N: <summary>` or `fix: <summary>`.
- Dependencies live in `pyproject.toml`; do not add one without a reason in the commit message.
- Prefer plain SQL files and psycopg over an ORM. dbt owns the transformations.

## Layout

See SPEC.md §9. Source in `src/ask_edgar/`, transformations in `dbt/`, evals in `evals/`,
fixtures for CI in `tests/fixtures/`.

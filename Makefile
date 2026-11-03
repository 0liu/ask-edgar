# ask-edgar — see CLAUDE.md for what each target is for.
# .env (gitignored) is loaded if present and exported to every recipe.
-include .env
export

.PHONY: up down data load dbt sanity check evals evals-live api mcp

up:
	docker compose up -d --wait

down:
	docker compose down

data:
	@test -f data/raw/2025q1.zip || { \
	    echo "Missing data/raw/2025q1.zip. Download it from"; \
	    echo "https://www.sec.gov/files/dera/data/financial-statement-data-sets/2025q1.zip"; \
	    exit 1; }
	unzip -o data/raw/2025q1.zip -d data/raw/2025q1

load:
	uv run ask-edgar load

dbt:
	cd dbt && uv run dbt build

sanity:
	docker compose exec -T postgres psql -U edgar -d edgar -f - < scripts/sanity.sql

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src
	uv run pytest -q

evals:
	uv run ask-edgar evals --replay

evals-live:
	uv run ask-edgar evals --live

api:
	uv run uvicorn ask_edgar.api:app --host 127.0.0.1 --port 8000 --reload

mcp:
	uv run ask-edgar-mcp

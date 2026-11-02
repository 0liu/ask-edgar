# Specification

Ask questions about US public-company financials in plain English. The agent writes SQL
against a Postgres warehouse built from the SEC's public **Financial Statement Data Sets**
(EDGAR), runs it read-only, and answers with the numbers and the query it used.
Every behaviour that matters is covered by an eval set that runs in CI.

Status: v0.1

---

## 1. Goal

Turn a public, notoriously awkward dataset (SEC FSDS: four denormalised TSV tables per
quarter, thousands of XBRL tags) into a clean relational mart, and put a tool-using LLM
agent in front of it that:

- answers factual questions about annual (10-K) financials correctly and says where the
  number came from;
- refuses questions the data cannot answer instead of guessing;
- measures: an eval set reports accuracy, SQL validity, tool-call count and cost, and
  the CI run fails if accuracy drops below the threshold.

## 2. Users and use cases

The users are people in a lending or investment business who need a figure from a company's
financial statements and do not write SQL: credit analysts checking a borrower's or guarantor's
financials, portfolio managers comparing companies in the same industry, operations and compliance
staff filling in a report. Traditionally the figure comes from opening the 10-K, finding the line
item, and copying it into Excel. When the same question comes up for twenty companies, someone
emails the one analyst who has the data loaded. The number is usually right, but nobody can say
afterwards which filing, which tag, or which fiscal period it came from.

`ask-edgar` is the "ask the data" layer for that workflow. The user asks in English; the agent
writes a query against a mart where every figure already carries its filing, tag and period; the
answer comes back with the SQL. An analyst can rerun or edit the query, a reviewer can see exactly
where the number came from, and a question that used to take twenty copy-pastes becomes one
sentence.

The same pattern applies to any internal dataset where staff ask the same kinds of questions over
and over: loan portfolios, investor records, compliance checklists. The SEC data is used here
because it is public, large, and awkward enough to show the data modelling that makes the agent
reliable; the agent, guard and evals are independent of the dataset.

Example questions (all answerable from the mart):

1. What was Apple's total revenue in fiscal year 2024?
2. Which company had the highest net income in FY2024?
3. List the five largest companies by total assets.
4. What was Microsoft's diluted EPS in FY2024?
5. How many companies in the dataset reported negative net income in FY2024?
6. What is Tesla's debt-to-equity ratio for FY2024? *(ratio computed in SQL)*
7. Compare operating cash flow and net income for Amazon in FY2024.
8. Which SIC industry has the most filers in the dataset?

Out of scope (the agent must say so): stock prices, forecasts, "should I buy", quarterly
figures (10-Q), anything before the loaded quarter's filings.

## 3. Non-goals (v0.1)

- No frontend. CLI, REST and MCP only.
- No multi-quarter history. One FSDS quarter (2025 Q1) is loaded.
- No quarterly (10-Q) figures; annual 10-K only.
- No fine-tuning, no RAG over filing text. The agent sees the schema, not the documents.
- No multi-user auth on the REST API.

## 4. Data

### 4.1 Source

SEC Financial Statement Data Sets, quarter **2025q1** (filings submitted Jan–Mar 2025,
which is when most calendar-year 10-Ks arrive).
Download: `https://www.sec.gov/files/dera/data/financial-statement-data-sets/2025q1.zip`
Contents: `sub.txt`, `num.txt`, `tag.txt`, `pre.txt`, `readme.htm`. Tab-separated, UTF-8.
The zip is **not** committed. `make data` expects it at `data/raw/2025q1.zip`.

### 4.2 Raw layer (schema `raw`) — data loaded as-is with `COPY`

| table | grain | key columns |
|---|---|---|
| `raw.sub` | one submission (filing) | `adsh` (PK), `cik`, `name`, `sic`, `form`, `period`, `fy`, `fp`, `fye`, `filed` |
| `raw.num` | one reported number | `adsh, tag, version, ddate, qtrs, uom, coreg` (composite PK) → `value` |
| `raw.tag` | one XBRL tag definition | `tag, version` (PK) → `custom`, `abstract`, `datatype`, `iord`, `crdr`, `tlabel` |
| `raw.pre` | one line on one statement | `adsh, report, line` (PK) → `stmt`, `tag`, `version`, `plabel` |

Column types follow the SEC readme (numeric `value`, date `ddate`/`period`/`filed` as
`YYYYMMDD` → `date`). Loader: pandas reads the TSV in chunks (`chunksize=500_000`),
casts, and streams each chunk to Postgres `COPY ... FROM STDIN`; memory stays flat
regardless of file size. Target: full quarter loads in < 5 min on a laptop.

### 4.3 Modelled layer (dbt, schema `analytics`)

Staging (views, 1:1 with raw, typed and renamed):
`stg_submissions`, `stg_numbers`, `stg_tags`, `stg_presentations`.

Seeds:
- `metric_map.csv` — maps XBRL tags to canonical metrics with a priority, e.g.

  | metric | tag | priority |
  |---|---|---|
  | revenue | Revenues | 1 |
  | revenue | RevenueFromContractWithCustomerExcludingAssessedTax | 2 |
  | revenue | SalesRevenueNet | 3 |
  | net_income | NetIncomeLoss | 1 |
  | total_assets | Assets | 1 |
  | total_liabilities | Liabilities | 1 |
  | stockholders_equity | StockholdersEquity | 1 |
  | stockholders_equity | StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest | 2 |
  | operating_income | OperatingIncomeLoss | 1 |
  | operating_cash_flow | NetCashProvidedByUsedInOperatingActivities | 1 |
  | cash | CashAndCashEquivalentsAtCarryingValue | 1 |
  | eps_diluted | EarningsPerShareDiluted | 1 |
  | eps_basic | EarningsPerShareBasic | 1 |
  | long_term_debt | LongTermDebt | 1 |
  | long_term_debt | LongTermDebtNoncurrent | 2 |
  | shares_outstanding | CommonStockSharesOutstanding | 1 |
  | cost_of_revenue | CostOfRevenue | 1 |
  | gross_profit | GrossProfit | 1 |
  | r_and_d | ResearchAndDevelopmentExpense | 1 |
  | sga | SellingGeneralAndAdministrativeExpense | 1 |
  | income_tax | IncomeTaxExpenseBenefit | 1 |
  | capex | PaymentsToAcquirePropertyPlantAndEquipment | 1 |
  | dividends_paid | PaymentsOfDividends | 1 |

  (Start with ~25 metrics; extend when evals show gaps.)

Marts (tables):

- `dim_company` — one row per `cik`: `cik`, `name`, `sic`, `sic_description` (seed),
  `state_of_incorporation`, `fiscal_year_end`, `latest_adsh`.
- `fct_financials` (long) — one row per `(cik, fiscal_year, metric)`:
  `cik`, `company_name`, `adsh`, `form`, `fiscal_year`, `fiscal_period`, `period_end`,
  `metric`, `value`, `uom`, `source_tag`, `qtrs`.
  Rules: `form IN ('10-K','10-K/A')`; consolidated only (`coreg IS NULL`); balance-sheet
  metrics use `qtrs = 0` at `ddate = period`; flow metrics use `qtrs = 4` ending at
  `period`; when several tags map to one metric, keep the lowest `priority` present;
  if the same filer has a 10-K and a 10-K/A for the same `fiscal_year`, keep the latest
  `filed`.
- `fct_financials_wide` (view) — one row per `(cik, fiscal_year)` with one column per
  metric. This is what the agent queries most of the time.

dbt tests: `unique` + `not_null` on the grain keys; `accepted_values` on `metric`;
`relationships` from `fct_financials.cik` to `dim_company.cik`; a singular test that
`fct_financials_wide` has ≥ 1,000 rows for `fiscal_year = 2024`.

## 5. Agent

### 5.1 Model and loop

Anthropic Messages API with tool use (`claude-sonnet-4-5` by default; configurable).
Max **5** tool calls per question, then a forced final answer. Temperature 0.

System prompt states: the scope (annual 10-K financials from SEC FSDS 2025q1, fiscal
years mostly 2024 and 2023), the tables and metrics available, the rule "query before you
answer; never state a number you did not read from a query result", and the refusal rule
for out-of-scope questions.

### 5.2 Tools

| tool | input | output | notes |
|---|---|---|---|
| `get_schema` | — | DDL of `analytics.dim_company`, `fct_financials`, `fct_financials_wide`; the metric list with one-line definitions; 3 sample rows of the wide view | cached per process |
| `run_query` | `sql: str` | `{columns, rows, row_count, truncated}` | read-only guard (5.3) |

### 5.3 SQL guard (enforced in code, not by prompt)

- Parse with `sqlglot`; reject anything that is not a single `SELECT` (no `;`, no CTE
  that writes, no `INTO`).
- Allow only `analytics.*` relations (and CTE names defined in the query).
- Execute in a transaction with `SET TRANSACTION READ ONLY`, `statement_timeout = 5000`.
- Append `LIMIT 50` when absent; `truncated = true` when hit.
- Return the Postgres error text to the model on failure so it can repair the query
  (counts toward the 5-call budget).

### 5.4 Output contract

```json
{"answer": "Apple's FY2024 revenue was $391.0B.", "sql": "SELECT ...", "caveats": ["..."],
 "in_scope": true, "tool_calls": 2}
```

`in_scope = false` for refusals, with `answer` explaining what the data covers.

## 6. Interfaces

- **CLI** — `ask-edgar ask "What was Apple's revenue in FY2024?"` (Typer). `--json` for
  the raw contract. Also `ask-edgar load`, `ask-edgar evals [--live|--replay]`.
- **REST** — FastAPI, `POST /ask {"question": "..."}` → output contract; `GET /health`;
  `GET /schema`.
- **MCP** — stdio server (official `mcp` Python SDK) exposing `get_schema`, `run_query`
  (same guard) and `get_metric(company, metric, fiscal_year)`. Registered in `.mcp.json`
  so Claude Code / Claude Desktop can use the warehouse directly.

## 7. Evals

`evals/questions.yaml` — 15 questions. Each entry:

```yaml
- id: q01
  question: What was Apple's total revenue in fiscal year 2024?
  answer_type: number          # number | text | list | refusal
  oracle_sql: >
    SELECT revenue FROM analytics.fct_financials_wide
    WHERE company_name ILIKE 'APPLE INC%' AND fiscal_year = 2024
  tolerance: 0.01              # relative, numbers only
```

Ground truth is **computed from the database by `oracle_sql`**, never hard-coded, so the
set stays valid if the metric map changes. The agent never sees `oracle_sql`.

Mix: 8 single-number lookups, 3 rankings/aggregations (`list`), 2 ratios computed in SQL,
2 out-of-scope questions that must return `in_scope = false` (`refusal`).

Scoring: number → within tolerance; text → case-insensitive containment; list → set
equality on the first column; refusal → `in_scope == false`.

Metrics reported (Markdown table written to `evals/results/latest.md` and pasted into
README): accuracy, SQL-valid-first-try rate, mean tool calls, mean latency, total cost
(from usage tokens × published prices).

Modes: `--live` calls the API and **records** every request/response pair to
`evals/cassettes/<id>.json`; `--replay` (default, used in CI) serves responses from the
cassettes, needs no key, costs nothing. CI fails if accuracy < 0.8.

## 8. Quality

- Python 3.13+, `uv`, `ruff` (lint + format), `mypy --strict` on `src/`, `pytest`.
- Unit tests: SQL guard (accept/reject cases), metric-map resolution, eval scorer, output
  parsing. Integration tests against Postgres (docker-compose service in CI).
- CI (GitHub Actions): `postgres:16` service → load **fixture subset** (`tests/fixtures/`:
  the four raw tables filtered to ~30 well-known CIKs, committed, < 2 MB) → `dbt build` →
  `pytest` → `ask-edgar evals --replay`.
- No secrets in the repo. `.env.example` lists `DATABASE_URL`, `ANTHROPIC_API_KEY`.
- Commits: conventional messages, one per phase, no trailers.

## 9. Repository layout

```
ask-edgar/
├── SPEC.md  PLAN.md  README.md  CLAUDE.md  .mcp.json  .env.example
├── pyproject.toml  uv.lock  Makefile  docker-compose.yml  .github/workflows/ci.yml
├── .claude/skills/add-eval/SKILL.md
├── src/ask_edgar/
│   ├── etl/        (download check, unzip, load_raw.py)
│   ├── db.py       (connection, read-only session)
│   ├── guard.py    (SQL guard)
│   ├── agent.py    (tool loop, prompt, output contract)
│   ├── recorder.py (cassette record/replay around the Anthropic client)
│   ├── cli.py  api.py  mcp_server.py
├── dbt/            (dbt_project.yml, models/staging, models/marts, seeds, tests)
├── evals/          (questions.yaml, run.py, cassettes/, results/)
└── tests/          (unit/, integration/, fixtures/)
```

## 10. Milestones

| # | milestone | done when |
|---|---|---|
| 0 | bootstrap | `make up` starts Postgres; `make check` (ruff+mypy+pytest) is green on an empty project |
| 1 | raw load | `ask-edgar load` loads 2025q1 in < 5 min; row counts printed |
| 2 | marts | `dbt build` green; `SELECT revenue FROM fct_financials_wide WHERE company_name ILIKE 'APPLE INC%' AND fiscal_year=2024` returns ~391e9 |
| 3 | agent + CLI | the 8 example questions answer correctly by hand |
| 4 | evals | `ask-edgar evals --live` ≥ 0.8; cassettes recorded; `--replay` reproduces |
| 5 | REST + MCP | `curl POST /ask` works; MCP server listed by `claude mcp list` and callable |
| 6 | CI | GitHub Actions green on the fixture subset |
| 7 | README | architecture diagram, quick start, eval table, design notes, limitations |

## 11. Open questions

- Model choice for the agent (Sonnet is the default; Haiku for cost if accuracy holds).
- Whether `fct_financials_wide` should also carry FY2023 columns for YoY questions (one more dbt model).

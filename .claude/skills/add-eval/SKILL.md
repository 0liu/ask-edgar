---
name: add-eval
description: Add a question to the eval set (evals/questions.yaml), with oracle SQL, and re-record its cassette.
---

# Add an eval question

1. Pick a question the marts can answer and whose result is the same on the CI fixture
   (`tests/fixtures/`, 30 companies, see `scripts/make_fixture.py`) as on the full quarter:
   name specific companies, or rank over a set whose top rows are all fixture companies.
   Use names whose `ILIKE 'NAME%'` pattern matches one filer; check with
   `select name from analytics.dim_company where name ilike 'NAME%'`.
2. Append an entry to `evals/questions.yaml`: next `id` (`q16`, ...), `question`,
   `answer_type` (`number | text | list | refusal`), `oracle_sql` over `analytics.*`
   (omit for refusal), `tolerance` for numbers (0.01; 0.02 for ratios). Keep the SPEC §7 mix
   in mind; `tests/unit/test_evals.py` asserts the counts, so update it when the mix changes.
3. Check the oracle by hand: `make sanity`-style, or `uv run --env-file .env ask-edgar ask`
   with the question and compare.
4. Record: `make evals-live` (calls the API, a few cents, rewrites every cassette). Read
   `evals/results/latest.md`; a failure usually means the question is ambiguous or the
   metric map lacks a tag, not that the scorer is wrong. Fix the prompt, the seed or the
   question, never the guard.
5. Replay: `make evals` must pass without an API key.
6. Commit `evals/questions.yaml`, `evals/cassettes/`, `evals/results/latest.md` and the
   test count change together.

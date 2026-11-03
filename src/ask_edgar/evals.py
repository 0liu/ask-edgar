"""The eval runner (SPEC §7): truth from oracle SQL, agent answers from live or replay.

Each question in ``evals/questions.yaml`` has an ``oracle_sql`` whose result, run against
the same database the agent queries, is the ground truth. Scoring by ``answer_type``:

* ``number``: a number in the answer text is within ``tolerance`` (relative) of the
  first cell of the oracle result;
* ``text``: the first cell appears in the answer text, case-insensitive;
* ``list``: the first column of the agent's own query equals the oracle's first column
  as a set, or every expected value is named in the answer text;
* ``refusal``: ``in_scope`` is false.
"""

import datetime as dt
import json
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, NamedTuple

import anthropic
import psycopg
import yaml
from psycopg.rows import TupleRow

from ask_edgar import agent, db, guard, recorder, tools

QUESTIONS_PATH = Path("evals/questions.yaml")
RESULTS_DIR = Path("evals/results")
ACCURACY_THRESHOLD = 0.8

type AnswerType = Literal["number", "text", "list", "refusal"]


class Price(NamedTuple):
    """USD per million tokens: input, output, cache write, cache read."""

    input: float
    output: float
    cache_write: float
    cache_read: float


# Published Claude API prices; unknown models get no cost figure rather than a wrong one.
PRICES = {
    "claude-sonnet-5-5": Price(2.00, 10.00, 2.50, 0.20),
    "claude-opus-5-5": Price(4.00, 20.00, 5.00, 0.20),
}


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    answer_type: AnswerType
    oracle_sql: str | None = None
    tolerance: float = 0.01


@dataclass
class Result:
    id: str
    answer_type: str
    passed: bool
    expected: str
    answer: str
    sql: str | None
    in_scope: bool
    tool_calls: int
    first_query_ok: bool | None
    latency_ms: int
    cost_usd: float | None
    note: str = ""


@dataclass
class Report:
    mode: str
    model: str
    run_at: str
    results: list[Result] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return sum(r.passed for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def sql_first_try_rate(self) -> float | None:
        tried = [r.first_query_ok for r in self.results if r.first_query_ok is not None]
        return sum(tried) / len(tried) if tried else None

    @property
    def mean_tool_calls(self) -> float:
        return statistics.fmean(r.tool_calls for r in self.results) if self.results else 0.0

    @property
    def mean_latency_ms(self) -> float:
        return statistics.fmean(r.latency_ms for r in self.results) if self.results else 0.0

    @property
    def total_cost_usd(self) -> float | None:
        costs = [r.cost_usd for r in self.results]
        return None if any(c is None for c in costs) else sum(c for c in costs if c is not None)

    def to_markdown(self) -> str:
        lines = [
            f"# Evals: {self.run_at}, {self.mode}, {self.model}",
            "",
            "| id | type | pass | tool calls | first SQL ok | latency | answer |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in self.results:
            first = "" if r.first_query_ok is None else ("yes" if r.first_query_ok else "no")
            answer = r.answer.replace("|", "\\|").replace("\n", " ")
            if r.note:
                answer += f" *({r.note})*"
            lines.append(
                f"| {r.id} | {r.answer_type} | {'pass' if r.passed else 'FAIL'} | "
                f"{r.tool_calls} | {first} | {r.latency_ms / 1000:.1f}s | {answer} |"
            )
        passed = sum(r.passed for r in self.results)
        first_try = self.sql_first_try_rate
        cost = self.total_cost_usd
        lines += [
            "",
            f"- accuracy: {passed}/{len(self.results)} = {self.accuracy:.2f}",
            "- SQL valid first try: " + ("n/a" if first_try is None else f"{first_try:.2f}"),
            f"- mean tool calls: {self.mean_tool_calls:.2f}",
            f"- mean latency: {self.mean_latency_ms / 1000:.1f}s",
            "- total cost: " + ("n/a" if cost is None else f"${cost:.4f}"),
        ]
        return "\n".join(lines) + "\n"

    def to_json(self) -> str:
        summary = {
            "mode": self.mode,
            "model": self.model,
            "run_at": self.run_at,
            "accuracy": self.accuracy,
            "sql_first_try_rate": self.sql_first_try_rate,
            "mean_tool_calls": self.mean_tool_calls,
            "mean_latency_ms": self.mean_latency_ms,
            "total_cost_usd": self.total_cost_usd,
            "results": [asdict(r) for r in self.results],
        }
        return json.dumps(summary, indent=2, ensure_ascii=False) + "\n"


def load_questions(path: Path = QUESTIONS_PATH) -> list[Question]:
    entries = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: expected a non-empty list of questions")
    questions = [Question(**entry) for entry in entries]
    ids = [q.id for q in questions]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: duplicate question ids")
    for q in questions:
        if q.answer_type not in ("number", "text", "list", "refusal"):
            raise ValueError(f"{q.id}: unknown answer_type {q.answer_type!r}")
        if q.answer_type != "refusal" and not q.oracle_sql:
            raise ValueError(f"{q.id}: oracle_sql is required for answer_type {q.answer_type}")
    return questions


def cost_usd(model: str, usage: agent.Usage) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_creation_input_tokens * price.cache_write
        + usage.cache_read_input_tokens * price.cache_read
    ) / 1_000_000


_NUMBER = re.compile(
    r"-?\$?\s?-?\d[\d,]*(?:\.\d+)?\s*(trillion|billion|million|thousand|[tbmk](?![a-z])|%)?",
    re.IGNORECASE,
)
_SCALE = {"t": 1e12, "b": 1e9, "m": 1e6, "k": 1e3}


def numbers_in(text: str) -> list[float]:
    """Every number in prose, with $, thousands separators, B/M/K words and % applied."""
    found: list[float] = []
    for match in _NUMBER.finditer(text):
        raw = match.group(0)
        suffix = (match.group(1) or "").lower()
        digits = raw[: len(raw) - len(match.group(1) or "")].replace("$", "").replace(",", "")
        value = float(digits.strip())
        if suffix == "%":
            found.append(value / 100)
        elif suffix:
            value *= _SCALE[suffix[0]]
        found.append(value)
    return found


def score(question: Question, answer: agent.Answer, truth: tools.QueryResult | None) -> Result:
    """Compare one answer with the oracle result; never raises on a bad answer."""
    result = Result(
        id=question.id,
        answer_type=question.answer_type,
        passed=False,
        expected="",
        answer=answer.answer,
        sql=answer.sql,
        in_scope=answer.in_scope,
        tool_calls=answer.tool_calls,
        first_query_ok=answer.first_query_ok,
        latency_ms=answer.latency_ms,
        cost_usd=cost_usd(answer.model, answer.usage),
    )
    if question.answer_type == "refusal":
        result.expected = "in_scope = false"
        result.passed = not answer.in_scope
        return result
    if truth is None or not truth.rows:
        result.note = "oracle returned no rows"
        return result
    first_cell = truth.rows[0][0]
    if question.answer_type == "number":
        if not isinstance(first_cell, int | float) or isinstance(first_cell, bool):
            result.note = f"oracle returned {first_cell!r}, not a number"
            return result
        result.expected = f"{first_cell:g}"
        allowed = abs(first_cell) * question.tolerance
        result.passed = any(abs(v - first_cell) <= allowed for v in numbers_in(answer.answer))
    elif question.answer_type == "text":
        expected = str(first_cell)
        result.expected = expected
        result.passed = expected.lower() in answer.answer.lower()
    else:
        expected_set = {str(row[0]) for row in truth.rows}
        result.expected = ", ".join(sorted(expected_set))
        result.passed = all(value.lower() in answer.answer.lower() for value in expected_set)
    return result


def list_matches_query(
    expected: set[str], sql: str | None, conn: psycopg.Connection[TupleRow]
) -> bool:
    """Whether the first column of the agent's own query equals ``expected`` as a set."""
    if sql is None:
        return False
    try:
        got = tools.run_query(sql, conn)
    except (guard.GuardError, psycopg.Error):
        return False
    return {str(row[0]) for row in got.rows} == expected


def run(
    questions: list[Question],
    *,
    live: bool,
    model: str | None = None,
    cassette_dir: Path = recorder.CASSETTE_DIR,
    conn: psycopg.Connection[TupleRow] | None = None,
    client: anthropic.Anthropic | None = None,
) -> Report:
    """Answer every question (live API or cassettes) and score it against the oracle."""
    model = agent.model_name(model)
    report = Report(
        mode="live" if live else "replay",
        model=model,
        run_at=dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )
    own = conn is None
    connection = db.connect() if conn is None else conn
    try:
        for question in questions:
            tape = recorder.Recorder(cassette_dir / f"{question.id}.json", live=live, client=client)
            started = time.perf_counter()
            answer = agent.ask(question.question, create=tape.create, model=model, conn=connection)
            answer.latency_ms = (
                int((time.perf_counter() - started) * 1000) + tape.replayed_latency_ms
            )
            if live:
                tape.save()
            truth = (
                tools.run_query(question.oracle_sql, connection) if question.oracle_sql else None
            )
            result = score(question, answer, truth)
            if question.answer_type == "list" and not result.passed and truth is not None:
                expected = {str(row[0]) for row in truth.rows}
                result.passed = list_matches_query(expected, answer.sql, connection)
            report.results.append(result)
    finally:
        if own:
            connection.close()
    return report


def write_results(report: Report, results_dir: Path = RESULTS_DIR) -> tuple[Path, Path]:
    results_dir.mkdir(parents=True, exist_ok=True)
    md = results_dir / "latest.md"
    js = results_dir / "latest.json"
    md.write_text(report.to_markdown(), encoding="utf-8")
    js.write_text(report.to_json(), encoding="utf-8")
    return md, js

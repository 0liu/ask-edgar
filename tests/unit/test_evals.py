"""Scoring, number parsing, cost, the report, and the questions file itself."""

import json
from pathlib import Path

import pytest

from ask_edgar import evals, tools
from ask_edgar.agent import Answer, Usage
from ask_edgar.evals import Question, numbers_in, score

MODEL = "claude-sonnet-5-5"


def answer(text: str, **kwargs: object) -> Answer:
    return Answer(answer=text, model=MODEL, **kwargs)  # type: ignore[arg-type]


def truth(*rows: list[tools.JsonValue]) -> tools.QueryResult:
    return tools.QueryResult(
        columns=["x"], rows=list(rows), row_count=len(rows), truncated=False, sql="SELECT 1"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("revenue of $350.0B", 350e9),
        ("exactly $350,018,000,000", 350018000000),
        ("a net loss of -$11.8B", -11.8e9),
        ("margin of 37.9%", 0.379),
        ("about 1.96 times net income", 1.96),
        ("EPS of $2.94 per share", 2.94),
        ("2.9 million shares", 2.9e6),
        ("$1,234.5M", 1.2345e9),
        ("4.3 trillion", 4.3e12),
        ("ratio 0.66", 0.66),
    ],
)
def test_numbers_in_prose(text: str, expected: float) -> None:
    assert any(abs(v - expected) <= 1e-9 * max(1, abs(expected)) for v in numbers_in(text))


def test_number_within_tolerance_passes() -> None:
    q = Question(id="q", question="?", answer_type="number", oracle_sql="x", tolerance=0.01)
    assert score(q, answer("Revenue was $350.0B in FY2024."), truth([350018000000])).passed
    assert not score(q, answer("Revenue was $340.0B."), truth([350018000000])).passed
    assert not score(q, answer("I could not find it."), truth([350018000000])).passed


def test_number_needs_a_numeric_oracle() -> None:
    q = Question(id="q", question="?", answer_type="number", oracle_sql="x")
    result = score(q, answer("$1"), truth([None]))
    assert not result.passed
    assert "not a number" in result.note
    result = score(q, answer("$1"), truth())
    assert "no rows" in result.note


def test_text_is_case_insensitive_containment() -> None:
    q = Question(id="q", question="?", answer_type="text", oracle_sql="x")
    assert score(
        q, answer("It is Pharmaceutical Preparations."), truth(["PHARMACEUTICAL PREPARATIONS"])
    ).passed
    assert not score(q, answer("It is Real Estate."), truth(["PHARMACEUTICAL PREPARATIONS"])).passed


def test_list_passes_when_every_expected_value_is_named() -> None:
    q = Question(id="q", question="?", answer_type="list", oracle_sql="x")
    expected = truth(["ALPHABET INC."], ["NVIDIA CORP"])
    assert score(q, answer("Top: ALPHABET INC. then NVIDIA CORP."), expected).passed
    result = score(q, answer("Top: ALPHABET INC. then Nvidia."), expected)
    assert not result.passed
    assert result.expected == "ALPHABET INC., NVIDIA CORP"


def test_refusal_checks_in_scope_only() -> None:
    q = Question(id="q", question="?", answer_type="refusal")
    assert score(q, answer("No prices here.", in_scope=False), None).passed
    assert not score(q, answer("The price is $5.", in_scope=True), None).passed


def test_cost_uses_published_prices() -> None:
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_creation_input_tokens=10_000,
        cache_read_input_tokens=1_000_000,
    )
    assert evals.cost_usd(MODEL, usage) == pytest.approx(2.0 + 1.0 + 0.025 + 0.2)
    assert evals.cost_usd("some-other-model", usage) is None


def test_report_metrics_and_rendering() -> None:
    report = evals.Report(mode="replay", model=MODEL, run_at="now")
    q_num = Question(id="q1", question="?", answer_type="number", oracle_sql="x")
    q_ref = Question(id="q2", question="?", answer_type="refusal")
    report.results.append(
        score(
            q_num,
            answer("$350.0B | fine", tool_calls=1, first_query_ok=True, latency_ms=1500),
            truth([350e9]),
        )
    )
    report.results.append(score(q_ref, answer("yes", in_scope=True), None))

    assert report.accuracy == 0.5
    assert report.sql_first_try_rate == 1.0
    assert report.mean_tool_calls == 0.5
    assert report.mean_latency_ms == 750
    md = report.to_markdown()
    assert "| q1 | number | pass | 1 | yes | 1.5s | $350.0B \\| fine |" in md
    assert "| q2 | refusal | FAIL |" in md
    assert "- accuracy: 1/2 = 0.50" in md
    assert "- total cost: $0.0000" in md
    payload = json.loads(report.to_json())
    assert payload["accuracy"] == 0.5
    assert [r["id"] for r in payload["results"]] == ["q1", "q2"]


def test_questions_file_matches_the_spec_mix() -> None:
    questions = evals.load_questions()
    assert len(questions) == 15
    types = [q.answer_type for q in questions]
    assert types.count("number") == 10  # 8 lookups + 2 ratios
    assert types.count("list") == 3
    assert types.count("refusal") == 2
    for q in questions:
        if q.answer_type != "refusal":
            assert q.oracle_sql and "analytics." in q.oracle_sql, q.id


def test_questions_file_validation(tmp_path: Path) -> None:
    bad = tmp_path / "q.yaml"
    bad.write_text("- id: a\n  question: x\n  answer_type: number\n")
    with pytest.raises(ValueError, match="oracle_sql is required"):
        evals.load_questions(bad)
    bad.write_text(
        "- id: a\n  question: x\n  answer_type: refusal\n"
        "- id: a\n  question: y\n  answer_type: refusal\n"
    )
    with pytest.raises(ValueError, match="duplicate"):
        evals.load_questions(bad)

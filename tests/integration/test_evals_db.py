"""Oracle SQL against the marts, and a full live-then-replay run with a fake model."""

import json
from pathlib import Path
from typing import Any

import psycopg
from anthropic.types import Message
from psycopg.rows import TupleRow

from ask_edgar import evals, tools
from ask_edgar.evals import Question

Q01 = Question(
    id="q01",
    question="What was Alphabet's total revenue in fiscal year 2024?",
    answer_type="number",
    oracle_sql=(
        "SELECT revenue FROM analytics.fct_financials_wide "
        "WHERE company_name ILIKE 'ALPHABET INC%' AND fiscal_year = 2024"
    ),
)
SQL = Q01.oracle_sql or ""


def test_every_oracle_returns_a_value(analytics_conn: psycopg.Connection[TupleRow]) -> None:
    for q in evals.load_questions():
        if q.oracle_sql:
            result = tools.run_query(q.oracle_sql, analytics_conn)
            assert result.rows and result.rows[0][0] is not None, q.id


def test_list_matches_query(analytics_conn: psycopg.Connection[TupleRow]) -> None:
    expected = {"ALPHABET INC.", "BERKSHIRE HATHAWAY INC", "NVIDIA CORP"}
    good = (
        "select company_name, net_income from analytics.fct_financials_wide "
        "where fiscal_year = 2024 order by net_income desc nulls last limit 3"
    )
    assert evals.list_matches_query(expected, good, analytics_conn)
    assert not evals.list_matches_query(
        expected, good.replace("limit 3", "limit 2"), analytics_conn
    )
    assert not evals.list_matches_query(expected, "select * from raw.sub", analytics_conn)
    assert not evals.list_matches_query(expected, None, analytics_conn)


class FakeMessages:
    """A model that queries Alphabet's revenue and then answers with it."""

    def __init__(self) -> None:
        self.responses = [
            Message.model_validate(
                {
                    "id": "m1",
                    "type": "message",
                    "role": "assistant",
                    "model": "fake",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "run_query", "input": {"sql": SQL}}
                    ],
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
            ),
            Message.model_validate(
                {
                    "id": "m2",
                    "type": "message",
                    "role": "assistant",
                    "model": "fake",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "answer": "FY2024 revenue was $350.0B.",
                                    "caveats": [],
                                    "in_scope": True,
                                }
                            ),
                        }
                    ],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 20, "output_tokens": 15},
                }
            ),
        ]
        self.calls: list[dict[str, Any]] = []

    def create(self, **request: Any) -> Message:
        self.calls.append(request)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self) -> None:
        self.messages = FakeMessages()


def test_live_then_replay_give_identical_results(
    analytics_conn: psycopg.Connection[TupleRow], tmp_path: Path
) -> None:
    fake = FakeClient()
    live = evals.run(
        [Q01],
        live=True,
        model="fake",
        cassette_dir=tmp_path,
        conn=analytics_conn,
        client=fake,  # type: ignore[arg-type]
    )
    assert live.accuracy == 1.0
    assert (tmp_path / "q01.json").is_file()
    assert len(fake.messages.calls) == 2
    tool_result = fake.messages.calls[1]["messages"][2]["content"][0]
    assert json.loads(tool_result["content"])["rows"] == [[350018000000]]

    replay = evals.run([Q01], live=False, model="fake", cassette_dir=tmp_path, conn=analytics_conn)
    assert replay.accuracy == 1.0
    assert replay.results[0].answer == live.results[0].answer
    assert replay.results[0].sql == live.results[0].sql
    assert replay.results[0].first_query_ok is True
    assert replay.results[0].cost_usd is None  # unknown model, no made-up price

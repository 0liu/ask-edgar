"""The tool loop, driven by a fake Anthropic client; no network, no database."""

import json
from typing import Any

import pytest
from anthropic.types import Message

from ask_edgar import agent, tools
from ask_edgar.agent import Answer, ask

SCHEMA = "-- fake schema text"
SQL = "select revenue from analytics.fct_financials_wide where cik = 1652044 and fiscal_year = 2024"


def tool_use(sql: str, tool_id: str = "toolu_1") -> Message:
    return Message.model_validate(
        {
            "id": "msg_tool",
            "type": "message",
            "role": "assistant",
            "model": "fake",
            "content": [
                {"type": "tool_use", "id": tool_id, "name": "run_query", "input": {"sql": sql}}
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
    )


def final(answer: str, *, in_scope: bool = True, caveats: list[str] | None = None) -> Message:
    text = json.dumps({"answer": answer, "caveats": caveats or [], "in_scope": in_scope})
    return text_message(text, stop_reason="end_turn")


def text_message(text: str, *, stop_reason: str, **extra: Any) -> Message:
    return Message.model_validate(
        {
            "id": "msg_final",
            "type": "message",
            "role": "assistant",
            "model": "fake",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "usage": {"input_tokens": 200, "output_tokens": 30, "cache_read_input_tokens": 150},
            **extra,
        }
    )


class FakeMessages:
    def __init__(self, responses: list[Message]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Message:
        self.calls.append(kwargs)
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[Message]) -> None:
        self.messages = FakeMessages(responses)


@pytest.fixture(autouse=True)
def fake_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "get_schema", lambda: SCHEMA)

    def run_query(sql: str, conn: Any = None) -> tools.QueryResult:
        if "boom" in sql:
            raise agent.guard.GuardError("relation raw.boom is not allowed")
        return tools.QueryResult(
            columns=["revenue"],
            rows=[[350018000000]],
            row_count=1,
            truncated=False,
            sql=sql.upper() + " LIMIT 50",
        )

    monkeypatch.setattr(tools, "run_query", run_query)


def run(responses: list[Message], **kwargs: Any) -> tuple[Answer, FakeClient]:
    client = FakeClient(responses)
    answer = ask("What was Alphabet's revenue?", client=client, **kwargs)  # type: ignore[arg-type]
    return answer, client


def test_query_then_answer() -> None:
    answer, client = run([tool_use(SQL), final("Alphabet's FY2024 revenue was $350.0B.")])

    assert answer.answer == "Alphabet's FY2024 revenue was $350.0B."
    assert answer.sql == SQL.upper() + " LIMIT 50"
    assert answer.in_scope is True
    assert answer.caveats == []
    assert answer.tool_calls == 1
    assert answer.usage.input_tokens == 300
    assert answer.usage.output_tokens == 50
    assert answer.usage.cache_read_input_tokens == 150
    assert answer.latency_ms >= 0

    first, second = client.messages.calls
    assert first["system"][0]["text"].rstrip().endswith(SCHEMA)
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert first["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert first["output_config"]["format"]["type"] == "json_schema"
    assert [t["name"] for t in first["tools"]] == ["get_schema", "run_query"]
    tool_result = second["messages"][2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "toolu_1"
    assert json.loads(tool_result["content"]) == {
        "columns": ["revenue"],
        "rows": [[350018000000]],
        "row_count": 1,
        "truncated": False,
    }
    assert "is_error" not in tool_result


def test_rejected_query_goes_back_as_an_error_and_is_not_the_reported_sql() -> None:
    answer, client = run(
        [tool_use("select * from raw.boom", "toolu_bad"), tool_use(SQL), final("done")]
    )
    error_result = client.messages.calls[1]["messages"][2]["content"][0]
    assert error_result["is_error"] is True
    assert error_result["content"].startswith("query rejected: relation raw.boom")
    assert answer.sql == SQL.upper() + " LIMIT 50"
    assert answer.tool_calls == 2


def test_budget_forces_a_final_answer() -> None:
    answer, client = run([tool_use(SQL), tool_use(SQL), final("forced")], max_tool_calls=2)
    choices = [call["tool_choice"] for call in client.messages.calls]
    assert choices == [
        {"type": "auto", "disable_parallel_tool_use": True},
        {"type": "auto", "disable_parallel_tool_use": True},
        {"type": "none"},
    ]
    assert answer.tool_calls == 2


def test_refusal_is_out_of_scope() -> None:
    refusal = text_message(
        "",
        stop_reason="refusal",
        stop_details={"type": "refusal", "category": None, "explanation": "policy"},
    )
    answer, _ = run([refusal])
    assert answer.in_scope is False
    assert answer.sql is None
    assert answer.caveats == ["policy"]


def test_out_of_scope_reply_passes_through() -> None:
    answer, _ = run([final("No stock prices here.", in_scope=False, caveats=["x"])])
    assert answer.in_scope is False
    assert answer.caveats == ["x"]
    assert answer.sql is None


def test_in_scope_answer_without_a_query_gets_a_caveat() -> None:
    answer, _ = run([final("Revenue was $1.")])
    assert answer.sql is None
    assert answer.caveats == [
        "Answered without running a query; the figures were not read from the data."
    ]


def test_malformed_final_reply_is_an_error() -> None:
    with pytest.raises(agent.AgentError, match="malformed"):
        run([text_message("not json", stop_reason="end_turn")])


def test_running_out_of_tokens_is_an_error() -> None:
    with pytest.raises(agent.AgentError, match="max_tokens"):
        run([text_message("", stop_reason="max_tokens")])


def test_get_schema_tool_returns_the_schema_text() -> None:
    schema_call = Message.model_validate(
        {
            "id": "msg_s",
            "type": "message",
            "role": "assistant",
            "model": "fake",
            "content": [{"type": "tool_use", "id": "toolu_s", "name": "get_schema", "input": {}}],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
    answer, client = run([schema_call, final("ok", in_scope=False)])
    assert client.messages.calls[1]["messages"][2]["content"][0]["content"] == SCHEMA
    assert answer.tool_calls == 1


def test_model_comes_from_argument_then_env_then_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(agent.MODEL_ENV, raising=False)
    assert agent.model_name() == agent.DEFAULT_MODEL
    monkeypatch.setenv(agent.MODEL_ENV, "claude-haiku-5-5")
    assert agent.model_name() == "claude-haiku-5-5"
    assert agent.model_name("explicit") == "explicit"


def test_system_prompt_states_the_rules_and_embeds_the_schema() -> None:
    prompt = agent.system_prompt(SCHEMA)
    assert "Query before you answer" in prompt
    assert "in_scope" in prompt
    assert prompt.rstrip().endswith(SCHEMA)
    assert "{schema}" not in prompt

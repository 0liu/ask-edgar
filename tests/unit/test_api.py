"""The REST interface, with the agent, schema and database check stubbed out."""

import pytest
from fastapi.testclient import TestClient

from ask_edgar import agent, api, db, tools
from ask_edgar.agent import Answer

client = TestClient(api.app)


def canned(question: str, **_: object) -> Answer:
    return Answer(
        answer=f"Answer to: {question}",
        sql="SELECT 1 LIMIT 50",
        caveats=["c"],
        in_scope=True,
        tool_calls=1,
        first_query_ok=True,
        model="claude-sonnet-5-5",
        latency_ms=12,
    )


def test_ask_returns_the_output_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "ask", canned)
    response = client.post("/ask", json={"question": "What was Alphabet's revenue?"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Answer to: What was Alphabet's revenue?"
    assert body["sql"] == "SELECT 1 LIMIT 50"
    assert body["in_scope"] is True
    assert body["tool_calls"] == 1
    assert set(body) >= {"answer", "sql", "caveats", "in_scope", "tool_calls", "usage", "model"}


def test_ask_validates_the_body() -> None:
    assert client.post("/ask", json={"question": ""}).status_code == 422
    assert client.post("/ask", json={}).status_code == 422


def test_ask_maps_failures_to_status_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(question: str, **_: object) -> Answer:
        raise agent.AgentError("malformed")

    monkeypatch.setattr(agent, "ask", broken)
    response = client.post("/ask", json={"question": "q"})
    assert response.status_code == 502
    assert "malformed" in response.json()["detail"]

    def no_db(question: str, **_: object) -> Answer:
        raise db.ConfigError("DATABASE_URL is not set")

    monkeypatch.setattr(agent, "ask", no_db)
    assert client.post("/ask", json={"question": "q"}).status_code == 503


def test_health(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "check_database", lambda: None)
    monkeypatch.delenv(agent.MODEL_ENV, raising=False)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "model": agent.DEFAULT_MODEL}

    def down() -> None:
        raise db.ConfigError("DATABASE_URL is not set")

    monkeypatch.setattr(api, "check_database", down)
    assert client.get("/health").status_code == 503


def test_schema_is_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "get_schema", lambda: "CREATE TABLE analytics.x ();")
    response = client.get("/schema")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "CREATE TABLE analytics.x ();"

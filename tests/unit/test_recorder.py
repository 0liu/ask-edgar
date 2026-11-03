"""Cassette record/replay: keys, round-trips through to_param, misses."""

import json
from pathlib import Path
from typing import Any

import pytest
from anthropic.types import Message

from ask_edgar.recorder import CassetteMissError, Recorder, request_key, serialise


def message(text: str, *, stop_reason: str = "end_turn", msg_id: str = "msg_1") -> Message:
    return Message.model_validate(
        {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "model": "fake",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


class FakeMessages:
    def __init__(self, responses: list[Message]) -> None:
        self.responses = list(responses)

    def create(self, **_: Any) -> Message:
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses: list[Message]) -> None:
        self.messages = FakeMessages(responses)


REQUEST: dict[str, Any] = {
    "model": "fake",
    "max_tokens": 10,
    "messages": [{"role": "user", "content": "q"}],
}


def test_live_records_and_replay_serves_the_same_response(tmp_path: Path) -> None:
    path = tmp_path / "q01.json"
    live = Recorder(path, live=True, client=FakeClient([message("hi")]))  # type: ignore[arg-type]
    recorded = live.create(**REQUEST)
    live.save()

    saved = json.loads(path.read_text())
    assert [entry["key"] for entry in saved] == [request_key(REQUEST)]
    assert saved[0]["request"]["messages"] == REQUEST["messages"]
    assert saved[0]["latency_ms"] >= 0

    replay = Recorder(path, live=False)
    assert replay.create(**REQUEST) == recorded
    assert replay.replayed_latency_ms == saved[0]["latency_ms"]
    with pytest.raises(CassetteMissError, match="evals-live"):
        replay.create(**{**REQUEST, "messages": [{"role": "user", "content": "other"}]})


def test_replayed_messages_chain_to_the_same_keys(tmp_path: Path) -> None:
    """A replayed response, passed back via to_param, must reproduce the next key."""
    path = tmp_path / "q.json"
    first = message("step one", stop_reason="tool_use", msg_id="m1")
    second = message("step two", msg_id="m2")
    live = Recorder(path, live=True, client=FakeClient([first, second]))  # type: ignore[arg-type]
    r1 = live.create(**REQUEST)
    follow_up = {**REQUEST, "messages": [*REQUEST["messages"], r1.to_param()]}
    r2 = live.create(**follow_up)
    live.save()

    replay = Recorder(path, live=False)
    p1 = replay.create(**REQUEST)
    p2 = replay.create(**{**REQUEST, "messages": [*REQUEST["messages"], p1.to_param()]})
    assert p2 == r2


def test_key_is_independent_of_dict_order_and_handles_sdk_blocks() -> None:
    block = message("x").to_param()
    a = request_key({"model": "m", "messages": [block]})
    b = request_key({"messages": [block], "model": "m"})
    assert a == b
    assert serialise({"messages": [block]})["messages"][0]["content"][0]["text"] == "x"


def test_live_mode_starts_from_an_empty_cassette(tmp_path: Path) -> None:
    path = tmp_path / "q.json"
    path.write_text('[{"key": "stale"}]')
    assert Recorder(path, live=True).exchanges == []
    assert Recorder(path, live=False).exchanges == [{"key": "stale"}]


def test_missing_cassette_misses_cleanly(tmp_path: Path) -> None:
    with pytest.raises(CassetteMissError, match="0 recorded"):
        Recorder(tmp_path / "none.json", live=False).create(**REQUEST)

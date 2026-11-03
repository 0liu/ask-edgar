"""Record/replay around ``messages.create`` for the evals (SPEC §7).

A cassette is one JSON file per eval question holding every request/response pair the
agent made, each keyed by a SHA256 of the request body. In live mode ``Recorder.create``
calls the API and records the exchange; in replay mode it serves the recorded response
for the same request and raises ``CassetteMissError`` for an unknown one, so CI never calls
the API by accident. Any change the model would see (question, prompt, schema, tools,
tool results, model id) changes the key and forces a re-record.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import anthropic
from anthropic.types import Message
from pydantic import BaseModel

CASSETTE_DIR = Path("evals/cassettes")


class CassetteMissError(LookupError):
    """Replay found no recorded response for a request."""


def serialise(value: object) -> Any:
    """JSON-ready copy of a request: SDK pydantic blocks become plain dicts."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): serialise(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [serialise(v) for v in value]
    return value


def request_key(request: dict[str, Any]) -> str:
    body = json.dumps(serialise(request), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class Recorder:
    """``recorder.create`` has the signature ``agent.ask`` expects for ``create``."""

    def __init__(
        self, path: Path, *, live: bool, client: anthropic.Anthropic | None = None
    ) -> None:
        self.path = path
        self.live = live
        self._client = client
        self.exchanges: list[dict[str, Any]] = []
        # API time of the replayed calls, so replay reports the latency the live run had.
        self.replayed_latency_ms = 0
        if not live and path.is_file():
            self.exchanges = json.loads(path.read_text(encoding="utf-8"))

    def create(self, **request: Any) -> Message:
        key = request_key(request)
        if self.live:
            client = self._client or anthropic.Anthropic()
            started = time.perf_counter()
            response: Message = client.messages.create(**request)
            self.exchanges.append(
                {
                    "key": key,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "request": serialise(request),
                    "response": response.model_dump(mode="json"),
                }
            )
            return response
        for exchange in self.exchanges:
            if exchange["key"] == key:
                self.replayed_latency_ms += int(exchange.get("latency_ms", 0))
                return Message.model_validate(exchange["response"])
        raise CassetteMissError(
            f"{self.path}: no recorded response for this request "
            f"({len(self.exchanges)} recorded). Run `make evals-live` to re-record."
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.exchanges, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

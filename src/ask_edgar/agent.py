"""The agent: a tool-use loop around the Anthropic Messages API (SPEC §5).

``ask`` sends the question with the system prompt (``prompts/system.md`` plus the
schema text), lets the model call ``get_schema`` and ``run_query`` up to
``MAX_TOOL_CALLS`` times, then returns an ``Answer``. The final reply is a
structured-output JSON object, so the contract never depends on parsing prose, and
the SQL reported is the last query that ran successfully, taken from the tool result
rather than from the model's text.
"""

import json
import os
import time
from importlib.resources import files
from typing import Any, Literal

import anthropic
import psycopg
from anthropic.types import (
    MessageParam,
    OutputConfigParam,
    TextBlockParam,
    ToolChoiceParam,
    ToolParam,
    ToolResultBlockParam,
)
from psycopg.rows import TupleRow
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ask_edgar import guard, tools

DEFAULT_MODEL = "claude-sonnet-5-5"
MODEL_ENV = "ASK_EDGAR_MODEL"
MAX_TOOL_CALLS = 5
MAX_TOKENS = 8192
EFFORT: Literal["low", "medium", "high", "xhigh", "max"] = "medium"

TOOLS: list[ToolParam] = [
    {
        "name": "get_schema",
        "description": (
            "The warehouse schema: DDL of the analytics tables, the metric list with "
            "definitions, and sample rows. The same text is already in your instructions."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "run_query",
        "description": (
            "Run one read-only SELECT against the analytics schema and return "
            "{columns, rows, row_count, truncated}. At most 50 rows come back "
            "(truncated = true when more matched). On failure the error text is returned "
            "so you can fix the query; each attempt counts toward the budget."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "A single SELECT over analytics.* tables, no semicolon.",
                }
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

# The shape of the model's final reply (structured output).
FINAL_REPLY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "caveats": {"type": "array", "items": {"type": "string"}},
        "in_scope": {"type": "boolean"},
    },
    "required": ["answer", "caveats", "in_scope"],
    "additionalProperties": False,
}


class AgentError(RuntimeError):
    """The model did not produce a usable reply."""


class FinalReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    caveats: list[str] = Field(default_factory=list)
    in_scope: bool = True


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def add(self, usage: anthropic.types.Usage) -> None:
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_creation_input_tokens += usage.cache_creation_input_tokens or 0
        self.cache_read_input_tokens += usage.cache_read_input_tokens or 0


class Answer(BaseModel):
    """The output contract (SPEC §5.4), plus what the evals need for cost and latency."""

    answer: str
    sql: str | None = None
    caveats: list[str] = Field(default_factory=list)
    in_scope: bool = True
    tool_calls: int = 0
    model: str
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0


def model_name(model: str | None = None) -> str:
    return model or os.environ.get(MODEL_ENV, "").strip() or DEFAULT_MODEL


def system_prompt(schema: str | None = None) -> str:
    """``prompts/system.md`` with the schema text filled in."""
    template = files("ask_edgar").joinpath("prompts/system.md").read_text(encoding="utf-8")
    return template.replace("{schema}", schema if schema is not None else tools.get_schema())


def ask(
    question: str,
    *,
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
    max_tool_calls: int = MAX_TOOL_CALLS,
    conn: psycopg.Connection[TupleRow] | None = None,
) -> Answer:
    """Answer ``question`` from the warehouse. Raises ``AgentError`` on a broken reply."""
    client = client or anthropic.Anthropic()
    model = model_name(model)
    started = time.perf_counter()
    system: list[TextBlockParam] = [
        {"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}
    ]
    messages: list[MessageParam] = [{"role": "user", "content": question}]
    output_config: OutputConfigParam = {
        "effort": EFFORT,
        "format": {"type": "json_schema", "schema": FINAL_REPLY_SCHEMA},
    }
    usage = Usage()
    tool_calls = 0
    last_sql: str | None = None

    while True:
        # Once the budget is spent the model may not call tools: it has to answer.
        tool_choice: ToolChoiceParam = (
            {"type": "auto", "disable_parallel_tool_use": True}
            if tool_calls < max_tool_calls
            else {"type": "none"}
        )
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            tools=TOOLS,
            tool_choice=tool_choice,
            output_config=output_config,
        )
        usage.add(response.usage)

        if response.stop_reason == "tool_use":
            messages.append(response.to_param())
            results: list[ToolResultBlockParam] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool_calls += 1
                result, sql_run = _run_tool(block.id, block.name, block.input, conn)
                if sql_run is not None:
                    last_sql = sql_run
                results.append(result)
            messages.append({"role": "user", "content": results})
            continue

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.stop_reason == "refusal":
            detail = response.stop_details.explanation if response.stop_details else None
            return Answer(
                answer=(
                    "The model declined to answer this question. ask-edgar covers annual "
                    "10-K financial statement figures from SEC filings made in January to "
                    "March 2025."
                ),
                caveats=[detail] if detail else [],
                in_scope=False,
                tool_calls=tool_calls,
                model=model,
                usage=usage,
                latency_ms=latency_ms,
            )
        if response.stop_reason not in ("end_turn", "stop_sequence"):
            raise AgentError(f"model stopped with {response.stop_reason!r} before answering")

        reply = _parse_final_reply(response)
        caveats = list(reply.caveats)
        if reply.in_scope and last_sql is None:
            caveats.append(
                "Answered without running a query; the figures were not read from the data."
            )
        return Answer(
            answer=reply.answer,
            sql=last_sql,
            caveats=caveats,
            in_scope=reply.in_scope,
            tool_calls=tool_calls,
            model=model,
            usage=usage,
            latency_ms=latency_ms,
        )


def _run_tool(
    tool_use_id: str, name: str, raw_input: object, conn: psycopg.Connection[TupleRow] | None
) -> tuple[ToolResultBlockParam, str | None]:
    """Execute one tool call. Returns the result block and the SQL that ran, if any."""

    def ok(content: str) -> ToolResultBlockParam:
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}

    def error(message: str) -> ToolResultBlockParam:
        return {**ok(message), "is_error": True}

    if name == "get_schema":
        return ok(tools.get_schema()), None
    if name != "run_query":
        return error(f"unknown tool {name!r}"), None
    arguments: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
    sql = arguments.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return error("run_query needs a non-empty 'sql' string"), None
    try:
        result = tools.run_query(sql, conn)
    except guard.GuardError as exc:
        return error(f"query rejected: {exc}"), None
    except psycopg.Error as exc:
        return error(f"query failed: {_pg_message(exc)}"), None
    return ok(result.to_json()), result.sql


def _pg_message(exc: psycopg.Error) -> str:
    primary = exc.diag.message_primary or str(exc).splitlines()[0]
    hint = exc.diag.message_hint
    return f"{primary} (hint: {hint})" if hint else primary


def _parse_final_reply(response: anthropic.types.Message) -> FinalReply:
    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        return FinalReply.model_validate_json(text)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise AgentError(f"model returned a malformed final reply: {exc}") from exc

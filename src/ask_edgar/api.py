"""REST interface (SPEC §6): ``POST /ask``, ``GET /health``, ``GET /schema``.

``make api`` serves it with uvicorn on port 8000. One worker is enough: each question is
a handful of API calls, and FastAPI runs these synchronous handlers in a thread pool.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import Literal

import anthropic
import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ask_edgar import agent, db, tools


def _version() -> str:
    try:
        return version("ask-edgar")
    except PackageNotFoundError:
        return "0"


app = FastAPI(
    title="ask-edgar",
    version=_version(),
    description=(
        "Ask questions about US public companies' annual 10-K financials in plain English. "
        "Answers come with the SQL that produced them."
    ),
)


class AskRequest(BaseModel):
    question: str = Field(
        min_length=1, max_length=1000, examples=["What was Alphabet's revenue in FY2024?"]
    )


class Health(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]
    model: str


def check_database() -> None:
    """Raise if the warehouse cannot be queried."""
    with db.read_only_cursor() as cur:
        cur.execute("select 1")


@app.post("/ask", summary="Answer one question")
def ask(body: AskRequest) -> agent.Answer:
    try:
        return agent.ask(body.question)
    except agent.AgentError as exc:
        raise HTTPException(status_code=502, detail=f"model reply: {exc}") from exc
    except anthropic.AnthropicError as exc:
        raise HTTPException(status_code=502, detail=f"model API: {exc}") from exc
    except (db.ConfigError, psycopg.Error, tools.SchemaError) as exc:
        raise HTTPException(status_code=503, detail=f"database: {exc}") from exc


@app.get("/health", summary="Liveness and database check")
def health() -> Health:
    try:
        check_database()
    except (db.ConfigError, psycopg.Error) as exc:
        raise HTTPException(status_code=503, detail=f"database: {exc}") from exc
    return Health(status="ok", database="ok", model=agent.model_name())


@app.get("/schema", summary="The schema text the agent sees", response_class=PlainTextResponse)
def schema() -> str:
    try:
        return tools.get_schema()
    except (db.ConfigError, psycopg.Error, tools.SchemaError) as exc:
        raise HTTPException(status_code=503, detail=f"database: {exc}") from exc

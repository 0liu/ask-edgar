"""Pure parts of the tool layer: value conversion and the result contract."""

import datetime as dt
import json
from decimal import Decimal

from ask_edgar import tools


def test_jsonable_converts_postgres_values() -> None:
    assert tools.jsonable(Decimal("350018000000.0000")) == 350018000000
    assert isinstance(tools.jsonable(Decimal("350018000000.0000")), int)
    assert tools.jsonable(Decimal("8.04")) == 8.04
    assert tools.jsonable(dt.date(2024, 12, 31)) == "2024-12-31"
    assert tools.jsonable(dt.datetime(2025, 2, 5, 16, 30)) == "2025-02-05T16:30:00"
    assert tools.jsonable(None) is None
    assert tools.jsonable(True) is True
    assert tools.jsonable("ALPHABET INC.") == "ALPHABET INC."
    assert tools.jsonable(object()).startswith("<object")


def test_query_result_contract_excludes_the_sql() -> None:
    result = tools.QueryResult(
        columns=["cik", "revenue"],
        rows=[[1652044, 350018000000]],
        row_count=1,
        truncated=False,
        sql="SELECT cik, revenue FROM analytics.fct_financials_wide LIMIT 50",
    )
    payload = json.loads(result.to_json())
    assert payload == {
        "columns": ["cik", "revenue"],
        "rows": [[1652044, 350018000000]],
        "row_count": 1,
        "truncated": False,
    }

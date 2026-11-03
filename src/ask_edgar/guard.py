"""SQL guard: the only path from the model to the database (SPEC §5.3).

``validate`` parses the model's SQL with sqlglot and returns a normalised statement
that is safe to run, or raises ``GuardError`` with a one-line reason the model can act
on. It accepts exactly one SELECT (set operations included) that touches only
``analytics.*`` relations and CTEs defined in the query, refuses functions that read
files, sleep or control other sessions, and caps the row count with a LIMIT.
Execution adds ``READ ONLY`` and a statement timeout (``db.py``); the guard decides
what reaches the server at all.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

ALLOWED_SCHEMA = "analytics"
MAX_ROWS = 50
DIALECT = "postgres"

# A READ ONLY transaction does not stop these: they read the file system, block the
# session, or act on other sessions. The list is a backstop; the real fix is a
# database role with nothing but SELECT on analytics.*.
DENIED_FUNCTIONS = frozenset(
    {
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "pg_ls_logdir",
        "pg_ls_waldir",
        "lo_import",
        "lo_export",
        "lo_get",
        "lo_put",
        "lo_unlink",
        "lo_create",
        "lo_creat",
        "lo_open",
        "dblink",
        "dblink_exec",
        "dblink_connect",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "set_config",
        "pg_notify",
        "pg_advisory_lock",
        "pg_advisory_xact_lock",
    }
)


class GuardError(ValueError):
    """The SQL was rejected. ``str(exc)`` is a short reason written for the model."""


def validate(sql: str, *, max_rows: int = MAX_ROWS) -> str:
    """Return ``sql`` normalised and capped at ``max_rows`` rows, or raise ``GuardError``.

    An existing ``LIMIT`` (or ``FETCH FIRST``) of at most ``max_rows`` is kept; a larger
    or missing one becomes ``LIMIT max_rows``.
    """
    root = _parse(sql)
    ctes = {cte.alias.lower() for cte in root.find_all(exp.CTE)}
    for node in root.walk():
        _check_node(node, ctes)
    return _cap_rows(root, max_rows).sql(dialect=DIALECT, pretty=True)


def _parse(sql: str) -> exp.Query:
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except ParseError as exc:
        raise GuardError(f"SQL could not be parsed: {str(exc).splitlines()[0]}") from exc
    if not statements:
        raise GuardError("the query is empty")
    if len(statements) > 1:
        raise GuardError("send one statement at a time (no ';' between statements)")
    root = statements[0]
    if not isinstance(root, exp.Select | exp.SetOperation):
        raise GuardError(f"only SELECT statements are allowed, not {_statement_name(root)}")
    return root


def _statement_name(node: exp.Expr) -> str:
    if isinstance(node, exp.Command):
        return str(node.this).upper()
    return node.key.upper()


def _check_node(node: exp.Expr, ctes: set[str]) -> None:
    if isinstance(node, exp.DML | exp.DDL):
        raise GuardError(f"{node.key.upper()} is not allowed, only SELECT")
    if isinstance(node, exp.Into):
        raise GuardError("SELECT INTO is not allowed")
    if isinstance(node, exp.Lock):
        raise GuardError("FOR UPDATE / FOR SHARE is not allowed")
    if isinstance(node, exp.Table):
        _check_table(node, ctes)
    elif isinstance(node, exp.Func):
        name = node.name.lower() if isinstance(node, exp.Anonymous) else node.sql_name().lower()
        if name in DENIED_FUNCTIONS:
            raise GuardError(f"function {name}() is not allowed")


def _check_table(table: exp.Table, ctes: set[str]) -> None:
    if not isinstance(table.this, exp.Identifier):
        raise GuardError("table functions are not allowed; query the analytics tables")
    if table.catalog:
        raise GuardError(f"write {ALLOWED_SCHEMA}.{table.name} without a database prefix")
    schema = table.db.lower()
    if schema == ALLOWED_SCHEMA or (not schema and table.name.lower() in ctes):
        return
    shown = ".".join(part for part in (table.db, table.name) if part)
    raise GuardError(
        f"relation {shown} is not allowed; only tables in schema {ALLOWED_SCHEMA} "
        f"(write {ALLOWED_SCHEMA}.{table.name})"
    )


def _cap_rows(root: exp.Query, max_rows: int) -> exp.Query:
    current = root.args.get("limit")
    if current is not None:
        existing = _limit_value(current)
        if existing is not None and existing <= max_rows:
            return root
    return root.limit(max_rows)


def _limit_value(node: exp.Expr) -> int | None:
    """The row count of a LIMIT / FETCH FIRST clause when it is a plain integer."""
    if isinstance(node, exp.Limit):
        value = node.expression
    elif isinstance(node, exp.Fetch):
        value = node.args.get("count")
        if value is None:  # FETCH FIRST ROW ONLY
            return 1
    else:
        return None
    if isinstance(value, exp.Literal) and value.is_int:
        return int(value.this)
    return None

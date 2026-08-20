"""Reading a Snowflake warehouse -- everything provable without a live account.

``SqlAdapter`` never imports a driver; it takes anything DB-API-shaped, so
``from_snowflake`` is a thin wrapper: authenticate, hand the resulting cursor to
the same adapter DuckDB uses, done. What this file proves is exactly that
wrapper -- the query text sent, the case-folding applied, and the connection
being closed -- against a fake cursor that speaks the same shape a real one
does.

What it cannot prove is a live handshake to a real Snowflake account: this
project's sandbox blocks outbound connections to *.snowflakecomputing.com at
the network policy layer (confirmed against a real trial account -- a 403 on
every CONNECT attempt, not assumed). That one step needs a machine outside
this sandbox and is the one thing this file does not claim to cover.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from concordance.adapters import sql as sqladapter


class FakeCursor:
    """Enough of a DB-API cursor for ``SqlAdapter`` to run against.

    Answers are keyed by a distinctive substring of the query rather than the
    exact text, so the test does not become a character-for-character pin of
    SQL the adapter is free to phrase differently.
    """

    def __init__(self, log: list[str], answers: dict[str, list[tuple]]) -> None:
        self._log = log
        self._answers = answers
        self._rows: list[tuple] = []

    def execute(self, query: str) -> "FakeCursor":
        self._log.append(query)
        for needle, rows in self._answers.items():
            if needle in query:
                self._rows = rows
                return self
        self._rows = []
        return self

    def fetchall(self) -> list[tuple]:
        return self._rows

    def fetchone(self) -> tuple | None:
        return self._rows[0] if self._rows else None


class FakeSnowflakeConnection:
    """Stands in for ``snowflake.connector.connect``'s return value.

    A fresh cursor per call, matching a real connection: two open cursors on
    one connection do not share result state, so the adapter must not assume
    they do.
    """

    def __init__(self, answers: dict[str, list[tuple]]) -> None:
        self.log: list[str] = []
        self._answers = answers
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.log, self._answers)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_snowflake(monkeypatch: pytest.MonkeyPatch):
    """Installs a fake ``snowflake.connector`` module so ``from_snowflake``'s
    own import succeeds without the real driver ever dialing out."""
    created: list[FakeSnowflakeConnection] = []
    answers = {
        "information_schema.tables": [("BATCH",)],
        "information_schema.columns": [("BATCH", "BATCHID", "NUMBER")],
        "information_schema.views": [("OOS_RATE", "SELECT 1 AS oos_rate")],
    }

    def connect(**kwargs: Any) -> FakeSnowflakeConnection:
        connection = FakeSnowflakeConnection(answers)
        connection.kwargs = kwargs  # type: ignore[attr-defined]
        created.append(connection)
        return connection

    module = types.SimpleNamespace(connect=connect)
    package = types.ModuleType("snowflake")
    package.connector = module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "snowflake", package)
    monkeypatch.setitem(sys.modules, "snowflake.connector", module)
    return created


# -- the wrapper's own logic ---------------------------------------------------

def test_credentials_are_passed_through_to_the_driver_unchanged(fake_snowflake) -> None:
    sqladapter.from_snowflake(
        account="acme-test123",
        user="test_user",
        password="s3cret",
        warehouse="COMPUTE_WH",
        database="ANALYTICS",
        schema="PUBLIC",
        role="ACCOUNTADMIN",
    )
    connection = fake_snowflake[0]
    assert connection.kwargs["account"] == "acme-test123"
    assert connection.kwargs["user"] == "test_user"
    assert connection.kwargs["password"] == "s3cret"
    assert connection.kwargs["warehouse"] == "COMPUTE_WH"
    assert connection.kwargs["role"] == "ACCOUNTADMIN"


def test_an_empty_role_is_omitted_rather_than_sent_as_an_empty_string(fake_snowflake) -> None:
    """An empty string role is not "no role" to every driver -- passing None is
    what actually means "use my default role"."""
    sqladapter.from_snowflake(
        account="a", user="u", password="p", warehouse="w", database="d", role=""
    )
    assert fake_snowflake[0].kwargs["role"] is None


def test_the_connection_is_closed_even_though_no_context_manager_is_visible(
    fake_snowflake,
) -> None:
    sqladapter.from_snowflake(
        account="a", user="u", password="p", warehouse="w", database="d"
    )
    assert fake_snowflake[0].closed is True


def test_the_connection_is_closed_even_when_extraction_raises(
    fake_snowflake, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(self, source=""):
        raise RuntimeError("boom")

    monkeypatch.setattr(sqladapter.SqlAdapter, "extract", explode)
    with pytest.raises(RuntimeError):
        sqladapter.from_snowflake(
            account="a", user="u", password="p", warehouse="w", database="d"
        )
    assert fake_snowflake[0].closed is True


# -- case folding: Snowflake's actual, documented behaviour -------------------

def test_database_and_schema_are_upper_cased_to_match_snowflakes_folding(
    fake_snowflake,
) -> None:
    """Snowflake folds an unquoted identifier to upper case and stores it that
    way in its information schema. A lower-case comparison here would match
    nothing and look identical to an empty warehouse, which is a far worse
    failure than the fix -- silently wrong is worse than loud and wrong."""
    sqladapter.from_snowflake(
        account="a", user="u", password="p", warehouse="w",
        database="analytics", schema="public",
    )
    queried = "\n".join(fake_snowflake[0].log)
    assert "'ANALYTICS'" in queried
    assert "'PUBLIC'" in queried
    assert "'analytics'" not in queried
    assert "'public'" not in queried


# -- what actually comes back, read through the same adapter DuckDB uses -----

def test_the_extracted_model_carries_real_tables_columns_and_measures(
    fake_snowflake,
) -> None:
    model = sqladapter.from_snowflake(
        account="a", user="u", password="p", warehouse="w", database="ANALYTICS"
    )
    assert [t.name for t in model.tables if not t.is_measure_only] == ["BATCH"]
    assert [c.name for c in model.columns] == ["BATCHID"]
    assert [m.name for m in model.measures] == ["OOS_RATE"]
    # Parsed with the snowflake sqlglot dialect, not silently defaulted to
    # duckdb's -- the two do not always agree on function spelling.
    assert model.measures[0].expression == "SELECT 1 AS oos_rate"


def test_the_model_name_defaults_to_the_schema_like_every_other_platform(
    fake_snowflake,
) -> None:
    model = sqladapter.from_snowflake(
        account="a", user="u", password="p", warehouse="w",
        database="ANALYTICS", schema="reporting",
    )
    assert model.name == "REPORTING"

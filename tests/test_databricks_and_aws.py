"""Reading Databricks, Redshift and Athena -- everything provable without an account.

``SqlAdapter`` never imports a driver; it takes anything DB-API-shaped, so each
of these connectors is a thin wrapper: authenticate, hand the resulting cursor
to the same adapter DuckDB uses, close. What this file proves is exactly that
wrapper -- the parameters sent to each driver, the identifier case folding, the
dialect selected, the catalog filtering, and the connection being closed on both
the success and the failure path -- against fake cursors that speak the same
DB-API shape a real driver does.

What it cannot prove is a live handshake to a real account. This project's
sandbox blocks the relevant hosts at the network policy layer, the same way it
blocks *.snowflakecomputing.com, so authenticating for real has to happen from a
machine outside that policy. That is the one step these tests do not claim to
cover, and it is stated here rather than left to be discovered.

The case-folding tests are the ones most worth having. A schema passed in the
wrong case matches nothing in ``information_schema`` and returns an empty model
-- which looks exactly like a schema that genuinely has no tables, not like a
bug. Nothing about that failure is loud, so it is pinned here.
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
    exact text, so these tests do not become a character-for-character pin of
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


class FakeConnection:
    """Stands in for any of the three drivers' connection objects.

    A fresh cursor per call, matching a real connection: two open cursors on one
    connection do not share result state, so the adapter must not assume they do.
    """

    def __init__(self, answers: dict[str, list[tuple]]) -> None:
        self.log: list[str] = []
        self.kwargs: dict[str, Any] = {}
        self._answers = answers
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.log, self._answers)

    def close(self) -> None:
        self.closed = True


#: Lower-cased on purpose. Databricks, Redshift and Athena all fold unquoted
#: identifiers to lower case, so this is the shape their information schema
#: actually returns -- and a connector that upper-cased like Snowflake would
#: match none of it.
ANSWERS = {
    "information_schema.tables": [("batch",)],
    "information_schema.columns": [("batch", "batch_id", "bigint")],
    "information_schema.views": [("oos_rate", "SELECT 1 AS oos_rate")],
}


def _install(monkeypatch: pytest.MonkeyPatch, *, module_path: str, attribute: str,
             package: str = "") -> list[FakeConnection]:
    """Install a fake driver module so a connector's lazy import succeeds.

    Returns the list every connection built through it is appended to, so a
    test can assert on what the driver was actually handed.
    """
    created: list[FakeConnection] = []

    def connect(**kwargs: Any) -> FakeConnection:
        connection = FakeConnection(ANSWERS)
        connection.kwargs = kwargs
        created.append(connection)
        return connection

    module = types.ModuleType(module_path)
    setattr(module, attribute, connect)
    monkeypatch.setitem(sys.modules, module_path, module)

    # `from databricks import sql` needs the parent package to exist and to
    # carry the submodule as an attribute, not just the submodule in sys.modules.
    if package:
        parent = types.ModuleType(package)
        setattr(parent, module_path.rsplit(".", 1)[-1], module)
        monkeypatch.setitem(sys.modules, package, parent)

    return created


@pytest.fixture
def fake_databricks(monkeypatch: pytest.MonkeyPatch) -> list[FakeConnection]:
    return _install(
        monkeypatch, module_path="databricks.sql", attribute="connect", package="databricks"
    )


@pytest.fixture
def fake_redshift(monkeypatch: pytest.MonkeyPatch) -> list[FakeConnection]:
    return _install(monkeypatch, module_path="redshift_connector", attribute="connect")


@pytest.fixture
def fake_athena(monkeypatch: pytest.MonkeyPatch) -> list[FakeConnection]:
    return _install(monkeypatch, module_path="pyathena", attribute="connect")


# -- Databricks ---------------------------------------------------------------

def test_databricks_passes_its_credentials_through_unchanged(fake_databricks) -> None:
    sqladapter.from_databricks(
        server_hostname="adb-123.cloud.databricks.com",
        http_path="/sql/1.0/warehouses/abc123",
        access_token="dapi-secret",
        catalog="analytics",
        schema="qc",
    )
    kwargs = fake_databricks[0].kwargs
    assert kwargs["server_hostname"] == "adb-123.cloud.databricks.com"
    assert kwargs["http_path"] == "/sql/1.0/warehouses/abc123"
    assert kwargs["access_token"] == "dapi-secret"


def test_databricks_sets_the_catalog_on_the_connection_not_just_the_filter(
    fake_databricks,
) -> None:
    """The load-bearing one for Unity Catalog.

    Every catalog has its own information_schema, so the adapter's unqualified
    `FROM information_schema.tables` resolves against whatever catalog the
    session is in. Filtering by table_catalog in the WHERE clause is not enough
    -- without setting it on the connection the query reads the wrong catalog's
    metadata entirely and returns a confidently empty model.
    """
    sqladapter.from_databricks(
        server_hostname="h", http_path="p", access_token="t",
        catalog="analytics", schema="qc",
    )
    assert fake_databricks[0].kwargs["catalog"] == "analytics"
    assert fake_databricks[0].kwargs["schema"] == "qc"


def test_databricks_folds_identifiers_to_lower_case(fake_databricks) -> None:
    """Unity Catalog stores unquoted identifiers lower-cased. A schema passed
    as ANALYTICS would match nothing and look like an empty schema."""
    sqladapter.from_databricks(
        server_hostname="h", http_path="p", access_token="t",
        catalog="ANALYTICS", schema="QC",
    )
    issued = " ".join(fake_databricks[0].log)
    assert "table_schema = 'qc'" in issued
    assert "table_catalog = 'analytics'" in issued
    assert "'QC'" not in issued


def test_databricks_reads_the_model_through_the_shared_adapter(fake_databricks) -> None:
    model = sqladapter.from_databricks(
        server_hostname="h", http_path="p", access_token="t", schema="qc"
    )
    assert [t.name for t in model.tables] == ["batch", "qc"]
    assert [m.name for m in model.measures] == ["oos_rate"]


def test_databricks_closes_its_connection(fake_databricks) -> None:
    sqladapter.from_databricks(server_hostname="h", http_path="p", access_token="t")
    assert fake_databricks[0].closed is True


def test_databricks_closes_its_connection_even_when_extraction_raises(
    fake_databricks, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leaked warehouse connection is worse than a failed read -- Databricks
    bills for an open SQL warehouse."""
    monkeypatch.setattr(
        sqladapter.SqlAdapter, "extract",
        lambda self, source="": (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        sqladapter.from_databricks(server_hostname="h", http_path="p", access_token="t")
    assert fake_databricks[0].closed is True


# -- Redshift -----------------------------------------------------------------

def test_redshift_sends_a_username_and_password_when_both_are_given(fake_redshift) -> None:
    sqladapter.from_redshift(
        host="cluster.abc.us-east-1.redshift.amazonaws.com",
        database="analytics", user="reader", password="s3cret", schema="qc",
    )
    kwargs = fake_redshift[0].kwargs
    assert kwargs["user"] == "reader"
    assert kwargs["password"] == "s3cret"
    assert kwargs["host"] == "cluster.abc.us-east-1.redshift.amazonaws.com"
    assert kwargs["port"] == 5439
    assert "iam" not in kwargs


def test_redshift_switches_to_iam_when_no_password_is_supplied(fake_redshift) -> None:
    """The path a deployment inside AWS actually uses. Without this, running on
    an instance role would require inventing a password to store somewhere."""
    sqladapter.from_redshift(
        host="h", database="analytics", region="us-east-1", schema="qc"
    )
    kwargs = fake_redshift[0].kwargs
    assert kwargs["iam"] is True
    assert kwargs["region"] == "us-east-1"
    assert "password" not in kwargs


def test_redshift_iam_still_forwards_a_db_user_when_one_is_named(fake_redshift) -> None:
    sqladapter.from_redshift(host="h", database="d", user="analyst")
    kwargs = fake_redshift[0].kwargs
    assert kwargs["iam"] is True
    assert kwargs["db_user"] == "analyst"
    assert "password" not in kwargs


def test_redshift_folds_identifiers_to_lower_case(fake_redshift) -> None:
    sqladapter.from_redshift(
        host="h", database="ANALYTICS", user="u", password="p", schema="QC"
    )
    issued = " ".join(fake_redshift[0].log)
    assert "table_schema = 'qc'" in issued
    assert "table_catalog = 'analytics'" in issued


def test_redshift_closes_its_connection_even_when_extraction_raises(
    fake_redshift, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sqladapter.SqlAdapter, "extract",
        lambda self, source="": (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        sqladapter.from_redshift(host="h", database="d", user="u", password="p")
    assert fake_redshift[0].closed is True


# -- Athena -------------------------------------------------------------------

def test_athena_takes_no_credentials_and_relies_on_the_boto3_chain(fake_athena) -> None:
    """Deliberate: PyAthena resolves credentials from environment, instance or
    task role, or ~/.aws/credentials. Accepting an access key here would invite
    someone to store one where a role would have done."""
    sqladapter.from_athena(
        s3_staging_dir="s3://results-bucket/athena/", region="eu-west-1", schema="qc"
    )
    kwargs = fake_athena[0].kwargs
    assert kwargs["s3_staging_dir"] == "s3://results-bucket/athena/"
    assert kwargs["region_name"] == "eu-west-1"
    assert kwargs["schema_name"] == "qc"
    assert not {"aws_access_key_id", "aws_secret_access_key", "password"} & set(kwargs)


def test_athena_omits_the_workgroup_entirely_when_none_is_named(fake_athena) -> None:
    """An empty string is a workgroup name to Athena, not the absence of one."""
    sqladapter.from_athena(s3_staging_dir="s3://b/", region="eu-west-1")
    assert "work_group" not in fake_athena[0].kwargs


def test_athena_forwards_a_workgroup_when_one_is_named(fake_athena) -> None:
    sqladapter.from_athena(
        s3_staging_dir="s3://b/", region="eu-west-1", work_group="analytics-wg"
    )
    assert fake_athena[0].kwargs["work_group"] == "analytics-wg"


def test_athena_filters_on_the_glue_catalog_by_default(fake_athena) -> None:
    """Athena's table_catalog is the Glue catalog -- `awsdatacatalog` unless a
    federated one is registered -- not the Glue database, which is the schema."""
    sqladapter.from_athena(s3_staging_dir="s3://b/", region="eu-west-1", schema="QC")
    issued = " ".join(fake_athena[0].log)
    assert "table_catalog = 'awsdatacatalog'" in issued
    assert "table_schema = 'qc'" in issued


def test_athena_closes_its_connection_even_when_extraction_raises(
    fake_athena, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sqladapter.SqlAdapter, "extract",
        lambda self, source="": (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        sqladapter.from_athena(s3_staging_dir="s3://b/", region="eu-west-1")
    assert fake_athena[0].closed is True


# -- the dialect each one parses with ----------------------------------------

@pytest.mark.parametrize(
    "connector, fixture_name, kwargs, dialect",
    [
        ("from_databricks", "fake_databricks",
         {"server_hostname": "h", "http_path": "p", "access_token": "t"}, "databricks"),
        ("from_redshift", "fake_redshift",
         {"host": "h", "database": "d", "user": "u", "password": "p"}, "redshift"),
        ("from_athena", "fake_athena",
         {"s3_staging_dir": "s3://b/", "region": "eu-west-1"}, "athena"),
    ],
)
def test_each_platform_parses_with_its_own_dialect(
    connector, fixture_name, kwargs, dialect, request, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dialect decides how a view's SQL is parsed into tables and columns,
    which is what reconciliation compares. Passing the wrong one degrades
    quietly -- a definition that fails to parse becomes a coverage gap, not an
    error -- so which dialect reached the adapter is pinned here.
    """
    request.getfixturevalue(fixture_name)
    seen: dict[str, str] = {}
    original = sqladapter.SqlAdapter.__init__

    def record(self, connection, database="", schema="main",
               dialect=sqladapter.DEFAULT_DIALECT, name=""):
        seen["dialect"] = dialect
        original(self, connection, database=database, schema=schema,
                 dialect=dialect, name=name)

    monkeypatch.setattr(sqladapter.SqlAdapter, "__init__", record)
    getattr(sqladapter, connector)(**kwargs)
    assert seen["dialect"] == dialect


# -- the CLI's environment-variable resolution --------------------------------
#
# The connectors above are only reachable through `concordance reconcile
# --platform ...`, and that layer has its own logic worth pinning: which
# variables are mandatory, that optional ones are omitted rather than passed
# empty, and that a missing driver names the extra to install instead of
# surfacing a bare ImportError.

@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No AWS/Databricks variables leaking in from the real environment.

    Without this, a developer machine that happens to export AWS_REGION would
    make the 'missing variables' tests pass for the wrong reason.
    """
    from concordance import cli

    for variables in cli._PLATFORM_ENV.values():
        for variable in variables.values():
            monkeypatch.delenv(variable, raising=False)


def test_missing_variables_are_all_named_at_once(clean_env, capsys) -> None:
    """One round-trip per missing variable would be a worse experience than
    being handed the whole list."""
    from concordance import cli

    assert cli._warehouse_model("databricks", "default") is None
    reported = capsys.readouterr().err
    assert "DATABRICKS_SERVER_HOSTNAME" in reported
    assert "DATABRICKS_HTTP_PATH" in reported
    assert "DATABRICKS_TOKEN" in reported


def test_an_optional_variable_missing_is_not_an_error(
    clean_env, fake_databricks, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DATABRICKS_CATALOG is optional; its absence must let the connector's own
    default apply rather than block the connection."""
    from concordance import cli

    monkeypatch.setenv("DATABRICKS_SERVER_HOSTNAME", "h")
    monkeypatch.setenv("DATABRICKS_HTTP_PATH", "p")
    monkeypatch.setenv("DATABRICKS_TOKEN", "t")

    assert cli._warehouse_model("databricks", "qc") is not None
    assert fake_databricks[0].kwargs["catalog"] == "main"


def test_redshift_without_a_password_reaches_the_iam_path(
    clean_env, fake_redshift, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The distinction this whole 'omit empty values' rule exists for: an empty
    password is not 'no password' to the driver, and passing one would take the
    password path with a blank secret."""
    from concordance import cli

    monkeypatch.setenv("REDSHIFT_HOST", "h")
    monkeypatch.setenv("REDSHIFT_DATABASE", "analytics")

    assert cli._warehouse_model("redshift", "public") is not None
    kwargs = fake_redshift[0].kwargs
    assert kwargs["iam"] is True
    assert "password" not in kwargs


def test_a_non_numeric_port_is_refused_by_name(
    clean_env, fake_redshift, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from concordance import cli

    monkeypatch.setenv("REDSHIFT_HOST", "h")
    monkeypatch.setenv("REDSHIFT_DATABASE", "d")
    monkeypatch.setenv("REDSHIFT_PORT", "not-a-number")

    assert cli._warehouse_model("redshift", "public") is None
    assert "REDSHIFT_PORT" in capsys.readouterr().err


def test_a_numeric_port_reaches_the_driver_as_an_int(
    clean_env, fake_redshift, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concordance import cli

    monkeypatch.setenv("REDSHIFT_HOST", "h")
    monkeypatch.setenv("REDSHIFT_DATABASE", "d")
    monkeypatch.setenv("REDSHIFT_PORT", "5440")

    cli._warehouse_model("redshift", "public")
    assert fake_redshift[0].kwargs["port"] == 5440


def test_a_missing_driver_names_the_extra_to_install(
    clean_env, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """The lazy import's bare "No module named pyathena" says nothing about how
    to fix it. This is the one failure with an exact, printable remedy."""
    from concordance import cli
    from concordance.adapters import sql as sqladapter

    monkeypatch.setenv("ATHENA_S3_STAGING_DIR", "s3://b/")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setattr(
        sqladapter, "from_athena",
        lambda **kwargs: (_ for _ in ()).throw(ImportError("No module named 'pyathena'")),
    )

    assert cli._warehouse_model("athena", "default") is None
    reported = capsys.readouterr().err
    assert "concordance[athena]" in reported


def test_a_connection_failure_never_prints_the_password(
    clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A warehouse error is wrapped for readability -- that wrapper must not
    turn a failed connection into a credential leak in a log."""
    from concordance import cli
    from concordance.adapters import sql as sqladapter
    from concordance.adapters.base import SourceError

    monkeypatch.setenv("REDSHIFT_HOST", "cluster.example.com")
    monkeypatch.setenv("REDSHIFT_DATABASE", "analytics")
    monkeypatch.setenv("REDSHIFT_USER", "reader")
    monkeypatch.setenv("REDSHIFT_PASSWORD", "hunter2-should-not-appear")
    monkeypatch.setattr(
        sqladapter, "from_redshift",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )

    with pytest.raises(SourceError) as caught:
        cli._warehouse_model("redshift", "public")

    assert "hunter2-should-not-appear" not in str(caught.value)
    assert "cluster.example.com" in str(caught.value)


def test_every_platform_is_wired_end_to_end(clean_env) -> None:
    """A platform reachable from --platform but absent from any of the three
    tables would fail with a KeyError at the moment someone selected it."""
    from concordance import cli

    platforms = set(cli._PLATFORM_ENV)
    assert platforms == set(cli._PLATFORM_REQUIRED)
    assert platforms == set(cli._PLATFORM_CONNECTOR)
    # duckdb needs no credentials, so it is absent above but must still have a
    # default schema -- it is the one platform reached by the other branch.
    assert platforms | {"duckdb"} == set(cli._PLATFORM_DEFAULT_SCHEMA)

    for platform, connector in cli._PLATFORM_CONNECTOR.items():
        assert hasattr(sqladapter, connector), f"{platform} names a connector that is missing"
        required = set(cli._PLATFORM_REQUIRED[platform])
        assert required <= set(cli._PLATFORM_ENV[platform]), (
            f"{platform} requires a key with no environment variable behind it"
        )

"""Warehouse adapter -- Snowflake, Databricks, and anything else speaking SQL.

The problem statement asks for integration with Snowflake, Databricks and AWS.
What those platforms have in common is the part that matters here: an
information schema describing tables and columns, and view definitions carrying
the SQL that computes a metric. This adapter reads that shape, so a warehouse
view lands in the same ``SemanticModel`` a ``.pbix`` does and every downstream
feature -- documentation, the chatbot, drift, reconciliation -- works over it
unchanged. ``SqlAdapter`` itself never imports a platform-specific driver; it
takes anything with a DB-API-shaped ``execute``, which is what makes
``from_snowflake`` below a few lines rather than a parallel implementation.

Verification is honest about its limits. The SQL path is exercised end to end
against DuckDB, whose ``information_schema`` follows the SQL standard that
Snowflake and Databricks also implement, so the queries and the parsing are
genuinely tested. ``from_snowflake`` is unit-tested against a fake cursor that
speaks the same DB-API shape, which proves the query text, the case-folding and
the wiring -- everything within this process's control. What that cannot prove
is a live handshake to a real Snowflake account: this project's sandbox blocks
outbound connections to *.snowflakecomputing.com at the network policy layer
(a 403 on every CONNECT, confirmed against a real trial account, not assumed),
so authenticating for real has to happen from a machine outside that policy.
The distinction is recorded here rather than glossed over, and repeated in the
README.

Expressions are parsed with sqlglot into a real AST, for the same reason DAX is
lexed rather than pattern-matched: identifying which tables and columns feed a
metric cannot be done reliably on raw text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

import sqlglot
from sqlglot import exp

from concordance.adapters.base import is_measure_container
from concordance.fingerprint import fingerprint_parts, fingerprint_text
from concordance.model import Column, CoverageGap, Measure, SemanticModel, Table

#: DuckDB is the default because it implements the standard information schema
#: and needs no server, which makes the whole path testable offline.
DEFAULT_DIALECT = "duckdb"

#: Engines rewrite aggregates into their own spelling when they store a view,
#: and sqlglot parses anything it does not model as a generic function call.
#: DuckDB turns ``COUNT(*)`` into ``count_star()``, which would otherwise be
#: invisible to aggregation comparison -- and COUNT is exactly the aggregation
#: that distinguishes most warehouse metrics. Only names whose meaning is
#: unambiguous are listed; guessing here would invent agreement that is not
#: there.
_AGGREGATE_FUNCTION_NAMES = frozenset(
    {
        "COUNT_STAR",
        "COUNTSTAR",
        "COUNT_IF",
        "COUNTIF",
        "APPROX_COUNT_DISTINCT",
        "APPROXCOUNTDISTINCT",
    }
)

#: Only unquoted, ordinary identifiers are accepted where a schema or catalog
#: name is interpolated into a query. See ``_literal``.
_PLAIN_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


class SqlConnection(Protocol):
    """The slice of DB-API this adapter uses."""

    def execute(self, query: str) -> Any: ...


@dataclass(frozen=True)
class WarehouseView:
    """A view whose SELECT computes something a report would show."""

    name: str
    schema: str
    definition: str

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name


def _literal(value: str, what: str) -> str:
    """Guard an identifier that has to be interpolated into a query.

    Schema and catalog names cannot be passed as bind parameters portably --
    DuckDB takes ``?``, Snowflake defaults to ``%s``, and the placeholder an
    engine accepts is a property of its driver rather than of SQL. Rather than
    pick one and quietly break the others, the value is restricted to an
    ordinary unquoted identifier, which cannot carry a quote and so cannot
    change the shape of the statement. Anything else is refused loudly instead
    of being escaped and hoped for.
    """
    if not _PLAIN_IDENTIFIER.fullmatch(value or ""):
        raise ValueError(
            f"{what} must be a plain SQL identifier (letters, digits, _ or $): {value!r}"
        )
    return value


# -- expression analysis ------------------------------------------------------

def _body(sql: str, dialect: str) -> exp.Expression | None:
    """Parse a view definition down to the query it computes.

    Engines disagree on what ``information_schema.views.view_definition``
    holds: DuckDB and Snowflake return the whole ``CREATE VIEW x AS SELECT ...``
    statement, Databricks returns the SELECT alone. Left unwrapped the CREATE
    target counts as a source table, so every view would appear to read itself
    -- and since reconciliation flags a metric when its source tables differ,
    *every* comparison would come back divergent and the report would be worth
    nothing. Unwrapping is what keeps the signal real.
    """
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:
        return None
    if tree is None:
        return None
    if isinstance(tree, exp.Create):
        return tree.expression
    return tree


def canonicalise(sql: str, dialect: str = DEFAULT_DIALECT) -> str:
    """Reduce a SQL expression to a canonical form.

    Serves the same purpose as the DAX canonicaliser -- formatting, casing and
    whitespace are erased so that a reformatted query is recognisably the same
    query. It deliberately does *not* try to prove two differently-written
    queries are equivalent; that is undecidable in general, and pretending
    otherwise would make every downstream comparison untrustworthy.
    """
    body = _body(sql, dialect)
    if body is None:
        # An unparseable statement still needs a stable form, so fall back to
        # collapsed whitespace rather than failing the whole extraction.
        return " ".join(sql.split())
    return body.sql(dialect=dialect, normalize=True)


def references(sql: str, dialect: str = DEFAULT_DIALECT) -> tuple[frozenset[str], frozenset[str]]:
    """Tables and columns a statement reads."""
    body = _body(sql, dialect)
    if body is None:
        return frozenset(), frozenset()

    tables = {t.name for t in body.find_all(exp.Table) if t.name}
    columns = {c.name for c in body.find_all(exp.Column) if c.name}
    return frozenset(tables), frozenset(columns)


def aggregations(sql: str, dialect: str = DEFAULT_DIALECT) -> frozenset[str]:
    """Aggregate functions a statement applies."""
    body = _body(sql, dialect)
    if body is None:
        return frozenset()

    found = {type(node).__name__.upper() for node in body.find_all(exp.AggFunc)}
    for node in body.find_all(exp.Anonymous):
        name = str(node.name).upper()
        if name in _AGGREGATE_FUNCTION_NAMES:
            found.add(name)
    return frozenset(found)


class SqlAdapter:
    """Extracts a semantic model from a SQL warehouse."""

    source_type = "sql"

    def __init__(
        self,
        connection: SqlConnection,
        database: str = "",
        schema: str = "main",
        dialect: str = DEFAULT_DIALECT,
        name: str = "",
    ) -> None:
        self.connection = connection
        self.database = _literal(database, "database") if database else ""
        self.schema = _literal(schema, "schema")
        self.dialect = dialect
        self.name = name or schema or "warehouse"

    def extract(self, source: str = "") -> SemanticModel:
        """Read the information schema into a semantic model.

        ``source`` is accepted for interface compatibility with the file-based
        adapters and used as the model name when given.
        """
        model = SemanticModel(
            name=source or self.name,
            source_path=f"{self.database}.{self.schema}" if self.database else self.schema,
            source_type=self.source_type,
        )

        base_tables = self._read_base_tables()
        views = self._read_views()

        for table_name in base_tables:
            model.tables.append(
                Table(name=table_name, fingerprint=fingerprint_text(table_name))
            )

        # Every view's definition is attributed to one container so the measures
        # have a parent node in the graph, the same way a .pbix measure-only
        # table hosts measures that belong to no data entity. The schema's own
        # name is used because inventing one would put a table in the
        # documentation that nobody could find in the warehouse.
        if views and self.schema.casefold() not in {t.casefold() for t in base_tables}:
            model.tables.append(
                Table(
                    name=self.schema,
                    fingerprint=fingerprint_text(self.schema),
                    is_measure_only=is_measure_container(
                        has_measures=True, visible_columns=0
                    ),
                )
            )

        model.columns = self._read_columns(base_tables)
        model.measures = [self._as_measure(v) for v in views]
        model.coverage_gaps = self._coverage_gaps(views)
        return model

    # -- information schema ------------------------------------------------

    def _where(self) -> str:
        clause = f"table_schema = '{self.schema}'"
        # A schema name is not unique across catalogs. DuckDB attaches a
        # `system` catalog that also has a schema called `main`, holding a dozen
        # internal views; Snowflake and Databricks likewise repeat PUBLIC and
        # INFORMATION_SCHEMA in every database. Filtering on the schema alone
        # sweeps those in and they arrive looking exactly like user metrics.
        if self.database:
            clause += f" AND table_catalog = '{self.database}'"
        return clause

    def _read_base_tables(self) -> list[str]:
        # `information_schema.tables` lists views too, by design -- the standard
        # distinguishes them with table_type, not by leaving them out. Without
        # this filter every view would be documented as a stored table as well
        # as a metric.
        rows = self._query(
            "SELECT table_name FROM information_schema.tables "
            f"WHERE {self._where()} AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
        return [str(row[0]) for row in rows]

    def _read_columns(self, tables: list[str]) -> list[Column]:
        keep = {t.casefold() for t in tables}
        rows = self._query(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns "
            f"WHERE {self._where()} "
            "ORDER BY table_name, ordinal_position"
        )
        return [
            Column(
                table=str(table),
                name=str(column),
                data_type=str(data_type),
                fingerprint=fingerprint_parts(str(table), str(column), str(data_type)),
            )
            for table, column, data_type in rows
            # Views have columns too; they belong to the metric, not to a table.
            if str(table).casefold() in keep
        ]

    def _read_views(self) -> list[WarehouseView]:
        rows = self._query(
            "SELECT table_name, view_definition FROM information_schema.views "
            f"WHERE {self._where()} ORDER BY table_name"
        )
        return [
            WarehouseView(name=str(name), schema=self.schema, definition=str(definition or ""))
            for name, definition in rows
        ]

    def _query(self, sql: str) -> list[tuple]:
        cursor = self.connection.execute(sql)
        return list(cursor.fetchall())

    # -- mapping -------------------------------------------------------------

    def _as_measure(self, view: WarehouseView) -> Measure:
        """Treat a view as the warehouse's equivalent of a measure.

        A view named ``oos_rate`` whose SELECT computes a ratio is the same kind
        of object as a DAX measure of that name: a named, reusable definition of
        a business number. Mapping it this way is what lets the two be compared.

        Not every view is a metric -- one that returns many rows is closer to a
        curated dataset -- so this makes each view a *candidate*, and the
        reconciliation only draws a conclusion where a name matches something on
        the other platform.
        """
        tables, columns = references(view.definition, self.dialect)
        return Measure(
            table=self.schema,
            name=view.name,
            expression=view.definition.strip(),
            # Fingerprinted over the canonical SQL, so a reformatted view does
            # not read as a change -- the same guarantee the DAX side gives.
            fingerprint=fingerprint_text(canonicalise(view.definition, self.dialect)),
            depends_on_columns=frozenset((self.schema, c) for c in columns),
            depends_on_tables=frozenset(tables),
        )

    def _coverage_gaps(self, views: list[WarehouseView]) -> list[CoverageGap]:
        """Record what could not be read, rather than silently reporting less.

        A view whose definition comes back empty -- which happens when the
        connected role lacks rights over the owning schema -- would otherwise
        become a metric with no logic behind it, indistinguishable from one that
        genuinely computes nothing.
        """
        gaps: list[CoverageGap] = []

        unreadable = [v.name for v in views if not v.definition.strip()]
        if unreadable:
            gaps.append(
                CoverageGap(
                    feature="view definitions",
                    count=len(unreadable),
                    reason=(
                        "the information schema returned no SQL for these views, "
                        "usually a permissions limit on the connected role"
                    ),
                )
            )

        unparsed = [
            v.name
            for v in views
            if v.definition.strip() and _body(v.definition, self.dialect) is None
        ]
        if unparsed:
            gaps.append(
                CoverageGap(
                    feature="unparsed view definitions",
                    count=len(unparsed),
                    reason=(
                        f"sqlglot could not parse these as {self.dialect}; their tables "
                        "and columns are unknown, so they are not compared"
                    ),
                )
            )
        return gaps


def from_duckdb(
    connection: SqlConnection, schema: str = "main", name: str = ""
) -> SemanticModel:
    """Convenience for the local, credential-free path.

    The catalog is resolved from the connection rather than assumed, because
    without it DuckDB's internal ``system.main`` views are indistinguishable
    from the user's metrics.
    """
    database = str(connection.execute("SELECT current_database()").fetchone()[0])
    return SqlAdapter(
        connection, database=database, schema=schema, dialect="duckdb", name=name
    ).extract()


class _CursorConnection:
    """Adapts a connection that needs an explicit cursor to the single
    ``execute`` this adapter expects.

    DuckDB's connection can run a query directly, which is what ``SqlConnection``
    is shaped around; Snowflake's (like most DB-API drivers) only runs one
    through a cursor it hands out separately. A fresh cursor per call rather
    than one held across calls, because the adapter issues its three reads
    sequentially and a shared cursor would make the second overwrite the first
    result set before it is read.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, query: str) -> Any:
        cursor = self._connection.cursor()
        cursor.execute(query)
        return cursor


def from_snowflake(
    account: str,
    user: str,
    password: str,
    warehouse: str,
    database: str,
    schema: str = "PUBLIC",
    role: str = "",
    name: str = "",
) -> SemanticModel:
    """Read a Snowflake database the same way ``from_duckdb`` reads a file.

    Requires ``snowflake-connector-python``, imported here rather than at
    module level so nothing that only ever touches DuckDB is made to install a
    driver for a warehouse it does not use.

    Snowflake folds unquoted identifiers to upper case and stores them that way
    in its information schema; a schema typed in lower case here would compare
    against ``information_schema.tables`` and match nothing, which would look
    identical to an empty schema rather than a case mismatch. Upper-casing here
    is only wrong for a database or schema created with a quoted, genuinely
    lower-case name -- the caller can still pass that through unchanged since
    Python strings are case-sensitive by default.
    """
    import snowflake.connector

    connection = snowflake.connector.connect(
        account=account,
        user=user,
        password=password,
        warehouse=warehouse,
        database=database,
        role=role or None,
    )
    try:
        return SqlAdapter(
            _CursorConnection(connection),
            database=database.upper(),
            schema=schema.upper(),
            dialect="snowflake",
            name=name,
        ).extract()
    finally:
        connection.close()


# -- Databricks and AWS -------------------------------------------------------
#
# Each of the three below is the same five steps: import a driver lazily,
# authenticate, wrap the connection so one `execute` is enough, hand it to the
# same `SqlAdapter` every other platform uses, and close in a `finally`. That
# they are this short is the point -- `SqlAdapter` never imports a driver and
# never learns a vendor's name, so a platform is a connection function rather
# than a parallel implementation.
#
# Identifier case is the one genuinely per-platform detail, and getting it
# wrong is invisible rather than loud: a schema typed in the wrong case matches
# nothing in `information_schema`, which looks exactly like an empty schema
# rather than a mismatch. Snowflake folds unquoted identifiers to upper case
# (see `from_snowflake`); Databricks, Redshift and Athena all fold to lower.
# Each function normalises accordingly, which is only wrong for a name that was
# deliberately created quoted and mixed-case -- rare, and recoverable by the
# caller since Python strings are case-sensitive and nothing here re-folds a
# value that already matches.
#
# What none of these can claim: a verified live handshake. This project's
# sandbox blocks outbound connections to the relevant hosts at the network
# policy layer, the same way it blocks *.snowflakecomputing.com, so every one
# of these is tested against a fake DB-API cursor that proves the query text,
# the case folding, the credential pass-through and the connection lifecycle --
# everything inside this process -- and nothing about whether a real account
# accepts the credentials. That last step needs a machine outside this sandbox
# and is stated plainly here, in the README, and in the tests rather than left
# for someone to discover.


def from_databricks(
    server_hostname: str,
    http_path: str,
    access_token: str,
    catalog: str = "main",
    schema: str = "default",
    name: str = "",
) -> SemanticModel:
    """Read a Databricks SQL warehouse through Unity Catalog.

    Requires ``databricks-sql-connector``, imported here rather than at module
    level so nothing that only ever touches DuckDB installs a driver for a
    platform it does not use.

    ``catalog`` and ``schema`` are passed to the driver rather than only used
    for filtering, and that is load-bearing: Unity Catalog exposes a *separate*
    ``information_schema`` inside every catalog, so the adapter's unqualified
    ``FROM information_schema.tables`` resolves against whichever catalog the
    session is currently in. Without setting it on the connection, the query
    would silently read a different catalog's metadata -- or fail outright on a
    workspace whose default catalog the token cannot see.

    The legacy Hive metastore (catalog ``hive_metastore``) implements only part
    of ``information_schema`` and generally does not return view definitions;
    those views arrive with empty SQL and are reported as a coverage gap rather
    than silently documented as metrics that compute nothing.
    """
    from databricks import sql as databricks_sql

    connection = databricks_sql.connect(
        server_hostname=server_hostname,
        http_path=http_path,
        access_token=access_token,
        catalog=catalog,
        schema=schema,
    )
    try:
        return SqlAdapter(
            _CursorConnection(connection),
            database=catalog.lower(),
            schema=schema.lower(),
            dialect="databricks",
            name=name,
        ).extract()
    finally:
        connection.close()


def from_redshift(
    host: str,
    database: str,
    user: str = "",
    password: str = "",
    schema: str = "public",
    port: int = 5439,
    region: str = "",
    name: str = "",
) -> SemanticModel:
    """Read an Amazon Redshift cluster or Serverless workgroup.

    Requires ``redshift-connector``, AWS's own driver, imported lazily.

    Two authentication paths, chosen by what is supplied rather than by a flag:
    a user and password go straight through, while omitting both switches the
    driver to IAM, where it resolves credentials from the ordinary boto3 chain
    (environment, instance role, ``~/.aws/credentials``). IAM is the path a
    deployment inside AWS would actually use, and forcing a password there
    would mean inventing one to store.

    Redshift is Postgres-derived: it folds unquoted identifiers to lower case
    and implements the standard ``information_schema``, so the adapter's
    queries need no Redshift-specific spelling.

    One real limit worth knowing before trusting a result: a *late-binding*
    view (``WITH NO SCHEMA BINDING``) appears in ``information_schema.views``
    with an empty ``view_definition``. That is not a permissions problem and
    not this adapter guessing -- Redshift genuinely does not expose the SQL
    there. Those views are reported as a coverage gap, so they are visibly
    unread rather than counted as metrics with no logic.
    """
    import redshift_connector

    options: dict[str, Any] = {
        "host": host,
        "database": database,
        "port": port,
    }
    if user and password:
        options["user"] = user
        options["password"] = password
    else:
        # `iam=True` without explicit keys is what tells the driver to use the
        # standard AWS credential chain rather than to look for a password it
        # was never given.
        options["iam"] = True
        if user:
            options["db_user"] = user
        if region:
            options["region"] = region

    connection = redshift_connector.connect(**options)
    try:
        return SqlAdapter(
            _CursorConnection(connection),
            database=database.lower(),
            schema=schema.lower(),
            dialect="redshift",
            name=name,
        ).extract()
    finally:
        connection.close()


def from_athena(
    s3_staging_dir: str,
    region: str,
    schema: str = "default",
    catalog: str = "awsdatacatalog",
    work_group: str = "",
    name: str = "",
) -> SemanticModel:
    """Read Amazon Athena over its Glue Data Catalog.

    Requires ``pyathena``, imported lazily.

    No credentials are taken as arguments on purpose. PyAthena authenticates
    through boto3's standard chain -- environment variables, an instance or
    task role, ``~/.aws/credentials`` -- which is both how AWS expects a client
    to behave and the only path that works for a role-based deployment with no
    long-lived keys to pass. ``s3_staging_dir`` is not optional in the same
    way a password would be: Athena writes every result set to S3 before the
    client reads it, so a query cannot run without somewhere to put it.

    Athena's vocabulary differs from the rest: a Glue *database* is what the
    information schema calls a ``table_schema``, and ``table_catalog`` is the
    Glue catalog itself, ``awsdatacatalog`` unless a federated catalog is
    registered. The parameters are named for the information schema's terms so
    the mapping is explicit rather than something to infer.

    Unverified against a live account, and one specific thing to check first:
    Athena stores a view created through it as an encoded Presto plan in Glue,
    and exposes readable SQL in ``information_schema.views.view_definition``
    only on engine v2 and later. On an older workgroup those definitions may
    come back empty, in which case they are reported as a coverage gap rather
    than treated as metrics with no logic -- visibly unread, not silently
    wrong.
    """
    from pyathena import connect as athena_connect

    options: dict[str, Any] = {
        "s3_staging_dir": s3_staging_dir,
        "region_name": region,
        "schema_name": schema,
    }
    if work_group:
        options["work_group"] = work_group

    connection = athena_connect(**options)
    try:
        return SqlAdapter(
            _CursorConnection(connection),
            database=catalog.lower(),
            schema=schema.lower(),
            dialect="athena",
            name=name,
        ).extract()
    finally:
        connection.close()

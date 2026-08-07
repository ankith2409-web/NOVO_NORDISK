"""Warehouse adapter -- Snowflake, Databricks, and anything else speaking SQL.

The problem statement asks for integration with Snowflake, Databricks and AWS.
What those platforms have in common is the part that matters here: an
information schema describing tables and columns, and view definitions carrying
the SQL that computes a metric. This adapter reads that shape, so a warehouse
view lands in the same ``SemanticModel`` a ``.pbix`` does and every downstream
feature -- documentation, the chatbot, drift, reconciliation -- works over it
unchanged.

Verification is honest about its limits. The SQL path is exercised end to end
against DuckDB, whose ``information_schema`` follows the SQL standard that
Snowflake and Databricks also implement, so the queries and the parsing are
genuinely tested. What is *not* tested is authenticating to a real Snowflake or
Databricks account, because no credentials exist for this project; those
connectors are thin wrappers that swap the DB-API connection and reuse
everything below. The distinction is recorded here rather than glossed over,
and repeated in the README.

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

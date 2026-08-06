"""The platform-independent object model.

Everything downstream -- the graph, the fingerprints, the drift comparison --
speaks in these types. Adapters translate Power BI, Snowflake or Databricks into
them, which is what keeps a seventh source platform an adapter rather than a
rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ObjectKind(Enum):
    TABLE = "table"
    COLUMN = "column"
    MEASURE = "measure"
    CALCULATED_COLUMN = "calculated_column"
    RELATIONSHIP = "relationship"


@dataclass(frozen=True)
class Column:
    table: str
    name: str
    data_type: str
    #: DAX expression when this is a calculated column; None for a stored one.
    expression: str | None = None
    fingerprint: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.table}[{self.name}]"

    @property
    def is_calculated(self) -> bool:
        return self.expression is not None


@dataclass(frozen=True)
class Measure:
    table: str
    name: str
    expression: str
    fingerprint: str
    display_folder: str | None = None
    description: str | None = None
    #: Qualified (table, column) pairs this measure reads.
    depends_on_columns: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    #: Names of other measures this measure reads.
    depends_on_measures: frozenset[str] = field(default_factory=frozenset)

    @property
    def qualified_name(self) -> str:
        return f"{self.table}[{self.name}]"


@dataclass(frozen=True)
class Relationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: str
    cross_filter: str
    is_active: bool
    fingerprint: str

    @property
    def label(self) -> str:
        state = "" if self.is_active else " (inactive)"
        return (
            f"{self.from_table}[{self.from_column}] -> "
            f"{self.to_table}[{self.to_column}] {self.cardinality}{state}"
        )


@dataclass(frozen=True)
class Table:
    name: str
    fingerprint: str
    #: Power BI generates hidden date tables; they are model noise, not content.
    is_system: bool = False
    #: A container holding only measures, with no stored columns of its own.
    #: Common in curated models ("Analysis DAX"), and it should be documented as
    #: a grouping rather than as a data entity.
    is_measure_only: bool = False
    power_query: str | None = None


@dataclass
class SemanticModel:
    """One extracted model, ready to be turned into a graph."""

    name: str
    source_path: str
    source_type: str
    tables: list[Table] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)

    def user_tables(self) -> list[Table]:
        return [t for t in self.tables if not t.is_system]

    def summary(self) -> dict[str, int]:
        return {
            "tables": len(self.tables),
            "user_tables": len(self.user_tables()),
            "columns": len(self.columns),
            "calculated_columns": sum(1 for c in self.columns if c.is_calculated),
            "measures": len(self.measures),
            "relationships": len(self.relationships),
        }

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
    HIERARCHY = "hierarchy"
    #: An external system a table is loaded from -- a file, a warehouse, a
    #: service. Not part of the Power BI model itself, which is exactly why it
    #: earns a node: it is where the model stops and the platform beneath it
    #: begins, and a lineage that ends at the table cannot show that seam.
    SOURCE = "source"


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
    #: Tables read as a whole, as in COUNTROWS(Patient) or REMOVEFILTERS(Product).
    depends_on_tables: frozenset[str] = field(default_factory=frozenset)

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


@dataclass(frozen=True)
class HierarchyLevel:
    """One rung of a drill-down path, bound to the column that supplies it."""

    ordinal: int
    name: str
    column: str


@dataclass(frozen=True)
class Hierarchy:
    """A named drill-down path over columns of one table.

    Part of the semantic layer a BRD has to describe -- "users drill Category ->
    Subcategory -> Product" is a business requirement, not an implementation
    detail -- so it belongs in the graph alongside measures and joins.
    """

    table: str
    name: str
    levels: tuple[HierarchyLevel, ...]
    fingerprint: str
    is_hidden: bool = False
    display_folder: str | None = None
    description: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.table}[{self.name}]"

    @property
    def path(self) -> str:
        return " -> ".join(level.name for level in self.levels)


@dataclass(frozen=True)
class CoverageGap:
    """A model feature the source reports but this adapter does not yet extract.

    Recorded so that incomplete extraction is *visible* rather than silent. A
    graph that quietly omits a model's KPIs looks identical to one from a model
    that has none, and documentation generated from it would be confidently
    wrong -- exactly the failure mode this project exists to prevent.
    """

    feature: str
    count: int
    reason: str


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
    hierarchies: list[Hierarchy] = field(default_factory=list)
    coverage_gaps: list[CoverageGap] = field(default_factory=list)

    def user_tables(self) -> list[Table]:
        return [t for t in self.tables if not t.is_system]

    def user_hierarchies(self) -> list[Hierarchy]:
        system = {t.name for t in self.tables if t.is_system}
        return [h for h in self.hierarchies if h.table not in system]

    def summary(self) -> dict[str, int]:
        return {
            "tables": len(self.tables),
            "user_tables": len(self.user_tables()),
            "columns": len(self.columns),
            "calculated_columns": sum(1 for c in self.columns if c.is_calculated),
            "measures": len(self.measures),
            "relationships": len(self.relationships),
            "hierarchies": len(self.hierarchies),
            "user_hierarchies": len(self.user_hierarchies()),
        }

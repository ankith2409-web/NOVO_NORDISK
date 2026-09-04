"""The platform-independent object model.

Everything downstream -- the graph, the fingerprints, the drift comparison --
speaks in these types. Adapters translate Power BI, Snowflake or Databricks into
them, which is what keeps a seventh source platform an adapter rather than a
rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# The report layer's shapes are defined next to the parser that produces them,
# and named here because the model holds them. Re-exported so that `from
# concordance.model import Visual` works alongside every other object kind --
# a caller should not have to know which of these came from a different file.
from concordance.normalize.layout import ReportPage, Visual, VisualField as VisualField
from concordance.normalize.filters import ReportFilter


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
    #: A row-level security role. Earns a node because it changes what a figure
    #: *is* for a given reader, which no measure's own expression records.
    ROLE = "role"
    #: A calculation group item. Rewrites measures at query time, so a measure's
    #: documented expression is not the whole story wherever one applies.
    CALCULATION_ITEM = "calculation_item"
    #: A KPI's target and thresholds. Its own node because it carries DAX the
    #: measure it decorates does not, and moving a threshold changes which
    #: figures a report calls acceptable while that measure stays identical.
    KPI = "kpi"


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
    #: The DAX that produces this table's rows, when it is a calculated table.
    #: Such a table has no stored columns at all -- both its rows and its columns
    #: come from this expression -- so it is the only record of what it holds.
    dax_expression: str | None = None

    @property
    def is_calculated(self) -> bool:
        return self.dax_expression is not None


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
class TablePermission:
    """One table's row filter inside a security role.

    The expression is DAX and is fingerprinted like any other, because a
    change to it changes who sees which rows -- a silent edit here alters
    every figure a restricted reader sees while every measure's own
    fingerprint stays identical.
    """

    table: str
    expression: str
    fingerprint: str


@dataclass(frozen=True)
class SecurityRole:
    """A row-level security role and the row filters it applies.

    Documenting a model without these describes it as an unrestricted user
    sees it. Two readers running the same report under different roles get
    different numbers from identical DAX, and nothing in the measure explains
    why -- so the filters have to be part of the specification, not a footnote.
    """

    name: str
    #: `read`, `readRefresh`, `none` -- what the role may do model-wide.
    model_permission: str
    permissions: tuple[TablePermission, ...]
    fingerprint: str
    description: str | None = None
    #: Who belongs to this role, where the source records it. Power BI keeps
    #: membership in the service rather than the model for a published report,
    #: so an empty tuple means "not stated here", never "nobody".
    members: tuple[str, ...] = ()

    @property
    def restricted_tables(self) -> tuple[str, ...]:
        return tuple(p.table for p in self.permissions)


@dataclass(frozen=True)
class CalculationItem:
    """One rewrite rule within a calculation group."""

    name: str
    expression: str
    fingerprint: str
    ordinal: int = 0
    #: Applied to whatever measure the item wraps, when the model sets one.
    format_expression: str | None = None


@dataclass(frozen=True)
class CalculationGroup:
    """A table whose items rewrite measures at query time.

    The reason this cannot be left out of a specification: with a calculation
    group in the model, `[Revenue]` on a report is not necessarily the
    expression `[Revenue]` is defined as. The item selected in the slicer
    substitutes itself around it. A document that shows only the measure's own
    DAX is describing something the report may never actually evaluate.
    """

    table: str
    items: tuple[CalculationItem, ...]
    fingerprint: str
    precedence: int | None = None
    description: str | None = None


@dataclass(frozen=True)
class ColumnVariation:
    """An alternate drill path a column offers.

    A variation redirects what happens when a user drills a field: the column
    stays where it is, but expanding it walks a different hierarchy. That is a
    reporting behaviour a BRD should state, and it is invisible in the
    column's own definition.
    """

    table: str
    column: str
    name: str
    #: The hierarchy drilling walks instead, when it could be resolved.
    default_hierarchy: str | None = None
    is_default: bool = False

    @property
    def qualified_name(self) -> str:
        return f"{self.table}[{self.column}]"


@dataclass(frozen=True)
class Kpi:
    """A measure tracked against a target, with status and trend thresholds.

    The problem statement asks for KPIs by name, and a KPI is not the measure
    it decorates: the measure says what the number *is*, the KPI says what the
    business considers good. "Revenue" is a metric; "Revenue against a target
    of 10M, red below 80%" is a requirement, and the thresholds live only
    here. The expressions are DAX and are fingerprinted, because moving a
    threshold changes which figures a report calls acceptable while the
    measure behind it is untouched.
    """

    table: str
    measure: str
    target_expression: str
    status_expression: str
    trend_expression: str
    fingerprint: str
    description: str | None = None
    target_description: str | None = None
    status_description: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.table}[{self.measure}]"


@dataclass(frozen=True)
class ObjectPermission:
    """One column or table a role may or may not see at all.

    Distinct from `TablePermission`, which filters *rows*. This hides the
    object itself: a reader under this role does not merely see fewer rows,
    they cannot see the field exists. A document describing a column that half
    its audience cannot access is describing a model none of them use.
    """

    role: str
    table: str
    #: None when the whole table is secured rather than one column.
    column: str | None
    #: "None" hides the object; "Read" makes it explicitly visible.
    permission: str

    @property
    def target(self) -> str:
        return f"{self.table}[{self.column}]" if self.column else self.table

    @property
    def hides(self) -> bool:
        return self.permission.casefold() == "none"


@dataclass(frozen=True)
class PerspectiveMember:
    """One object exposed by a perspective."""

    #: "Table", "Column", "Measure" or "Hierarchy".
    object_kind: str
    table: str
    name: str


@dataclass(frozen=True)
class Perspective:
    """A named subset of the model shown to one audience.

    Worth documenting because a perspective is a scope statement: the Finance
    view of a model is a different product from the Operations view, and a
    requirements document that describes the union of both describes something
    nobody is actually given.
    """

    name: str
    members: tuple[PerspectiveMember, ...]
    fingerprint: str

    @property
    def tables(self) -> tuple[str, ...]:
        seen: list[str] = []
        for member in self.members:
            if member.table and member.table not in seen:
                seen.append(member.table)
        return tuple(seen)


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
    roles: list[SecurityRole] = field(default_factory=list)
    calculation_groups: list[CalculationGroup] = field(default_factory=list)
    kpis: list[Kpi] = field(default_factory=list)
    variations: list[ColumnVariation] = field(default_factory=list)
    object_permissions: list[ObjectPermission] = field(default_factory=list)
    perspectives: list[Perspective] = field(default_factory=list)
    coverage_gaps: list[CoverageGap] = field(default_factory=list)
    #: The report layer: which pages exist and which tiles are on them. Empty
    #: for a source that carries no report -- a .SemanticModel folder is the
    #: model alone, and a tile it does not describe must not be invented for it.
    report_pages: list[ReportPage] = field(default_factory=list)
    #: The filters the report applies before any of its numbers are computed.
    #: Without these a card's figure cannot be reconciled against the same
    #: measure computed here: Microsoft's Sales & Returns pins its whole report
    #: to June, so its `Net Sales` card reads 387K where the measure over every
    #: row of the file is 1.2M. Both are right; only one of them says which
    #: question it answered.
    report_filters: list[ReportFilter] = field(default_factory=list)

    def visuals(self) -> list[Visual]:
        """Every tile in the report, across all pages."""
        return [visual for page in self.report_pages for visual in page.visuals]

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
            "roles": len(self.roles),
            "calculation_items": sum(
                len(group.items) for group in self.calculation_groups
            ),
            "kpis": len(self.kpis),
            "perspectives": len(self.perspectives),
            "report_pages": len(self.report_pages),
            "report_filters": len(self.report_filters),
            "visuals": len(self.visuals()),
        }

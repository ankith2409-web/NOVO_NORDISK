"""Deriving requirements from an implemented semantic model.

This is the reverse direction the project is built around: rather than writing
requirements and hoping the implementation matches, requirements are *read out*
of what was actually built, each one bound to the object that satisfies it.

Two properties matter more than fluency:

* **Nothing is invented.** Every requirement is derived from a graph node that
  exists, and carries the evidence -- node id and fingerprint -- it came from.
  A language model cannot add a requirement here; it only rephrases what this
  module already decided.

* **Confidence is earned, not asserted.** A measure's calculation is *stated* by
  the model and can be reported as fact. Why a join exists is *inferred*, and is
  marked as such so a human confirms it rather than a document asserting it.

Requirement identifiers are derived from an object's identity -- its table and
name -- never from its expression or its position in a list. That way editing a
measure keeps its requirement id stable so drift can be reported against it,
while adding a measure does not renumber every requirement after it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from concordance.fingerprint import fingerprint_parts, short
from concordance.generate import patterns
from concordance.graph.csg import (
    SemanticGraph,
    calculation_item_id,
    hierarchy_id,
    measure_id,
    role_id,
    table_id,
)


class Confidence(Enum):
    """How much of this requirement came from the model versus from inference."""

    #: Stated outright by the model; the document can assert it.
    HIGH = "high"
    #: Follows from an explicit structural rule applied to a real object.
    MEDIUM = "medium"
    #: Business intent inferred from structure alone. Needs a human to confirm.
    LOW = "low"


class Kind(Enum):
    BUSINESS = "business"
    FUNCTIONAL = "functional"


@dataclass(frozen=True)
class Evidence:
    """The extracted fact a requirement rests on."""

    node_id: str
    fingerprint: str
    detail: str


@dataclass(frozen=True)
class Requirement:
    id: str
    kind: Kind
    category: str
    statement: str
    rationale: str
    confidence: Confidence
    evidence: tuple[Evidence, ...] = field(default_factory=tuple)

    @property
    def needs_review(self) -> bool:
        """Low-confidence requirements never enter a document unchallenged."""
        return self.confidence is Confidence.LOW

    @property
    def bound_fingerprints(self) -> tuple[str, ...]:
        return tuple(e.fingerprint for e in self.evidence)


def _identity(*parts: str) -> str:
    """Stable short id from an object's identity, independent of its content."""
    return short(fingerprint_parts(*parts))[:6]


class RequirementDeriver:
    """Reads a semantic graph and produces the requirements it implies."""

    def __init__(self, graph: SemanticGraph) -> None:
        self.graph = graph
        self.model = graph.model
        self._system_tables = {t.name for t in self.model.tables if t.is_system}

    def derive(self) -> list[Requirement]:
        out: list[Requirement] = []
        out.extend(self._from_measures())
        out.extend(self._from_relationships())
        out.extend(self._from_hierarchies())
        out.extend(self._from_calculated_columns())
        out.extend(self._from_roles())
        out.extend(self._from_calculation_groups())
        out.extend(self._from_model_shape())
        # Sorted for deterministic document order; ids stay stable regardless.
        return sorted(out, key=lambda r: (r.kind.value, r.category, r.id))

    # -- measures -> the metrics the business tracks -------------------------

    def _from_measures(self) -> list[Requirement]:
        out: list[Requirement] = []

        for measure in self.model.measures:
            node = measure_id(measure.table, measure.name)
            evidence = (
                Evidence(
                    node_id=node,
                    fingerprint=measure.fingerprint,
                    detail=measure.expression.strip(),
                ),
            )

            # A measure with no expression is broken, not simple. Asserting
            # "shall be implemented exactly as the expression recorded here"
            # when nothing is recorded is precisely the confidently-wrong
            # documentation this project exists to prevent.
            if not measure.expression.strip():
                out.append(
                    Requirement(
                        id=f"REQ-F-{_identity('measure', measure.table, measure.name)}",
                        kind=Kind.FUNCTIONAL,
                        category="Incomplete definitions",
                        statement=(
                            f"**{measure.qualified_name}** is declared but carries no "
                            f"expression. Its calculation must be supplied before this "
                            f"specification can be considered complete."
                        ),
                        rationale=(
                            "The model declares the measure without a definition, so "
                            "what it computes cannot be established from the model."
                        ),
                        confidence=Confidence.LOW,
                        evidence=evidence,
                    )
                )
                continue

            detected = patterns.detect(measure.expression)
            behaviour = detected[0] if detected else None

            columns = sorted(
                f"{table}[{column}]" for table, column in measure.depends_on_columns
            )
            upstream = sorted(measure.depends_on_measures)

            # BUSINESS: what the metric is, in plain terms. Phrased without an
            # article so every pattern label reads correctly -- "a ranking" and
            # "a conditional logic" cannot share a sentence frame.
            descriptor = (
                f" It applies {behaviour.label}, which {behaviour.description}."
                if behaviour
                else ""
            )
            source = ""
            if columns:
                source = f" It is calculated from {_join(columns)}."
            elif upstream:
                source = f" It is derived from {_join(f'[{m}]' for m in upstream)}."

            out.append(
                Requirement(
                    id=f"REQ-B-{_identity('measure', measure.table, measure.name)}",
                    kind=Kind.BUSINESS,
                    category="Metrics and KPIs",
                    statement=(
                        f"The solution shall report **{measure.name}** as a reportable "
                        f"metric.{descriptor}{source}"
                    ),
                    rationale=(
                        f"A measure named {measure.qualified_name} is defined in the "
                        f"model, so the business tracks this quantity."
                    ),
                    confidence=Confidence.HIGH,
                    evidence=evidence,
                )
            )

            # FUNCTIONAL: exactly how it must be computed.
            dependency_note = ""
            if upstream:
                dependency_note = (
                    f" It depends on the measures {_join(f'[{m}]' for m in upstream)}, "
                    f"which must be evaluated first."
                )
            out.append(
                Requirement(
                    id=f"REQ-F-{_identity('measure', measure.table, measure.name)}",
                    kind=Kind.FUNCTIONAL,
                    category="Measure definitions",
                    statement=(
                        f"**{measure.qualified_name}** shall be implemented exactly as "
                        f"the DAX expression recorded in the evidence for this "
                        f"requirement.{dependency_note}"
                    ),
                    rationale=(
                        "The expression is the authoritative definition of the metric; "
                        "any change to it changes the reported figure."
                    ),
                    confidence=Confidence.HIGH,
                    evidence=evidence,
                )
            )

        return out

    # -- relationships -> what can be analysed by what ------------------------

    def _from_relationships(self) -> list[Requirement]:
        out: list[Requirement] = []

        for rel in self.model.relationships:
            if rel.from_table in self._system_tables or rel.to_table in self._system_tables:
                continue

            ident = _identity(
                "relationship", rel.from_table, rel.from_column,
                rel.to_table, rel.to_column,
            )
            evidence = (
                Evidence(
                    node_id=f"{table_id(rel.from_table)} -> {table_id(rel.to_table)}",
                    fingerprint=rel.fingerprint,
                    detail=rel.label,
                ),
            )

            # BUSINESS: the analytical capability the join enables. Why the join
            # exists is genuinely inferred, so this is not asserted as fact.
            if rel.is_active:
                statement = (
                    f"Users shall be able to analyse **{rel.from_table}** by attributes "
                    f"of **{rel.to_table}**."
                )
                rationale = (
                    f"An active {rel.cardinality} relationship joins "
                    f"{rel.from_table}[{rel.from_column}] to "
                    f"{rel.to_table}[{rel.to_column}], which makes this slicing possible. "
                    f"The business purpose is inferred from the model's shape rather "
                    f"than stated by it."
                )
                confidence = Confidence.MEDIUM
            else:
                statement = (
                    f"An alternate, inactive relationship between **{rel.from_table}** "
                    f"and **{rel.to_table}** shall be available for calculations that "
                    f"explicitly invoke it."
                )
                rationale = (
                    "The relationship is present but inactive, so it only applies where "
                    "a calculation activates it deliberately. Its intended use case is "
                    "not recorded in the model and needs confirmation."
                )
                confidence = Confidence.LOW

            out.append(
                Requirement(
                    id=f"REQ-B-{ident}",
                    kind=Kind.BUSINESS,
                    category="Dimensional analysis",
                    statement=statement,
                    rationale=rationale,
                    confidence=confidence,
                    evidence=evidence,
                )
            )

            # FUNCTIONAL: the join as built.
            state = "active" if rel.is_active else "inactive"
            out.append(
                Requirement(
                    id=f"REQ-F-{ident}",
                    kind=Kind.FUNCTIONAL,
                    category="Data model relationships",
                    statement=(
                        f"A **{state}** {rel.cardinality} relationship shall join "
                        f"`{rel.from_table}[{rel.from_column}]` to "
                        f"`{rel.to_table}[{rel.to_column}]`, with "
                        f"**{rel.cross_filter}** cross-filter direction."
                    ),
                    rationale=(
                        "Cardinality and cross-filter direction determine how filters "
                        "propagate; changing either changes reported results."
                    ),
                    confidence=Confidence.HIGH,
                    evidence=evidence,
                )
            )

        return out

    # -- hierarchies -> drill paths -------------------------------------------

    def _from_hierarchies(self) -> list[Requirement]:
        out: list[Requirement] = []

        for hierarchy in self.model.user_hierarchies():
            ident = _identity("hierarchy", hierarchy.table, hierarchy.name)
            evidence = (
                Evidence(
                    node_id=hierarchy_id(hierarchy.table, hierarchy.name),
                    fingerprint=hierarchy.fingerprint,
                    detail=hierarchy.path,
                ),
            )

            # A hierarchy without levels defines no drill path. Left unguarded
            # this produced "following the path ." and "shall contain 0 levels
            # in this order: ." -- malformed text asserted at high confidence.
            if not hierarchy.levels:
                out.append(
                    Requirement(
                        id=f"REQ-F-{ident}",
                        kind=Kind.FUNCTIONAL,
                        category="Incomplete definitions",
                        statement=(
                            f"The **{hierarchy.name}** hierarchy on `{hierarchy.table}` "
                            f"is declared but defines no levels. Its drill path must be "
                            f"specified before this document is complete."
                        ),
                        rationale=(
                            "A hierarchy with no levels provides no navigation, so the "
                            "intended drill sequence cannot be read from the model."
                        ),
                        confidence=Confidence.LOW,
                        evidence=evidence,
                    )
                )
                continue

            out.append(
                Requirement(
                    id=f"REQ-B-{ident}",
                    kind=Kind.BUSINESS,
                    category="Drill-down and navigation",
                    statement=(
                        f"Users shall be able to drill through **{hierarchy.name}** on "
                        f"{hierarchy.table}, following the path "
                        f"{' → '.join(level.name for level in hierarchy.levels)}."
                    ),
                    rationale=(
                        "The hierarchy is explicitly defined in the model, including the "
                        "order of its levels, so this navigation is a stated capability."
                    ),
                    confidence=Confidence.HIGH,
                    evidence=evidence,
                )
            )

            out.append(
                Requirement(
                    id=f"REQ-F-{ident}",
                    kind=Kind.FUNCTIONAL,
                    category="Hierarchy definitions",
                    statement=(
                        f"The **{hierarchy.name}** hierarchy on `{hierarchy.table}` shall "
                        f"contain {len(hierarchy.levels)} levels in this order: "
                        + ", ".join(
                            f"{level.ordinal + 1}. {level.name} "
                            f"(`{hierarchy.table}[{level.column}]`)"
                            for level in hierarchy.levels
                        )
                        + "."
                    ),
                    rationale=(
                        "Level order defines the drill sequence; reordering the levels "
                        "produces a different navigation path for users."
                    ),
                    confidence=Confidence.HIGH,
                    evidence=evidence,
                )
            )

        return out

    # -- calculated columns ----------------------------------------------------

    def _from_calculated_columns(self) -> list[Requirement]:
        out: list[Requirement] = []

        for column in self.model.columns:
            if not column.is_calculated or column.table in self._system_tables:
                continue

            expression = (column.expression or "").strip()
            evidence = (
                Evidence(
                    node_id=f"column:{column.qualified_name}",
                    fingerprint=column.fingerprint,
                    detail=expression,
                ),
            )

            if not expression:
                out.append(
                    Requirement(
                        id=f"REQ-F-{_identity('column', column.table, column.name)}",
                        kind=Kind.FUNCTIONAL,
                        category="Incomplete definitions",
                        statement=(
                            f"The column `{column.qualified_name}` is marked as "
                            f"calculated but carries no expression. Its derivation must "
                            f"be supplied."
                        ),
                        rationale=(
                            "Without an expression the model does not say how the value "
                            "is produced, so it cannot be specified from the model."
                        ),
                        confidence=Confidence.LOW,
                        evidence=evidence,
                    )
                )
                continue

            out.append(
                Requirement(
                    id=f"REQ-F-{_identity('column', column.table, column.name)}",
                    kind=Kind.FUNCTIONAL,
                    category="Derived attributes",
                    statement=(
                        f"The column `{column.qualified_name}` shall be derived by "
                        f"calculation rather than loaded from source, using the "
                        f"expression recorded in the evidence."
                    ),
                    rationale=(
                        "The column is computed inside the model, so its definition is "
                        "part of the solution rather than of the upstream data."
                    ),
                    confidence=Confidence.HIGH,
                    evidence=evidence,
                )
            )

        return out

    # -- row-level security -----------------------------------------------------

    def _from_roles(self) -> list[Requirement]:
        """Who sees which rows -- a requirement no measure's own DAX records.

        Worth stating in both documents for different reasons. The business
        needs to know a restriction exists at all, because it changes what a
        figure *means* for a given reader. The functional side needs the
        filter expression itself, because that is the thing an implementer has
        to reproduce and the thing whose edit silently changes every number a
        restricted user sees.
        """
        out: list[Requirement] = []

        for role in self.model.roles:
            # The node id is the role's, not a per-permission one, because that
            # is what drift reports a change against -- evidence pointing at an
            # id no snapshot contains would leave the requirement silently
            # unlinked, which is the one failure this whole scheme exists to
            # prevent. The *fingerprint* stays per-permission, so a decision
            # recorded against one filter goes stale when that filter moves.
            evidence = tuple(
                Evidence(
                    node_id=role_id(role.name),
                    fingerprint=p.fingerprint,
                    detail=f"{p.table}: {p.expression}",
                )
                for p in role.permissions
            ) or (
                Evidence(
                    node_id=role_id(role.name),
                    fingerprint=role.fingerprint,
                    detail=f"modelPermission: {role.model_permission}",
                ),
            )
            ident = _identity("role", role.name)
            tables = sorted({p.table for p in role.permissions})

            if tables:
                statement = (
                    f"Users assigned the **{role.name}** role shall see only the rows "
                    f"of {_join(f'**{t}**' for t in tables)} that the role's filter "
                    f"admits."
                )
                rationale = (
                    f"The model defines a row-level security role named {role.name} "
                    f"with a filter on {_join(tables)}, so figures reported to a member "
                    f"of this role are computed over a subset of the data."
                )
            else:
                # A role with no table filters still restricts: it governs
                # model-wide permission. Saying "sees only the rows the filter
                # admits" when there is no filter would be plainly false.
                statement = (
                    f"The **{role.name}** role shall grant "
                    f"*{role.model_permission}* access to the model without "
                    f"restricting any table's rows."
                )
                rationale = (
                    f"The role is defined with modelPermission "
                    f"{role.model_permission} and no table filters."
                )

            out.append(
                Requirement(
                    id=f"REQ-B-{ident}",
                    kind=Kind.BUSINESS,
                    category="Access and visibility",
                    statement=statement,
                    rationale=rationale,
                    confidence=Confidence.HIGH,
                    evidence=evidence,
                )
            )

            for permission in role.permissions:
                if not permission.expression:
                    continue
                out.append(
                    Requirement(
                        id=f"REQ-F-{_identity('role', role.name, permission.table)}",
                        kind=Kind.FUNCTIONAL,
                        category="Row-level security",
                        statement=(
                            f"Access to **{permission.table}** under the "
                            f"**{role.name}** role shall be filtered by the DAX "
                            f"expression recorded in the evidence for this "
                            f"requirement."
                        ),
                        rationale=(
                            "The filter is evaluated for every query a member of this "
                            "role runs, so it determines the figures they see; any "
                            "change to it changes those figures without changing any "
                            "measure."
                        ),
                        confidence=Confidence.HIGH,
                        evidence=(
                            Evidence(
                                node_id=role_id(role.name),
                                fingerprint=permission.fingerprint,
                                detail=permission.expression,
                            ),
                        ),
                    )
                )

        return out

    # -- calculation groups -----------------------------------------------------

    def _from_calculation_groups(self) -> list[Requirement]:
        """The rewrites that make a measure's own expression not the whole story."""
        out: list[Requirement] = []

        for group in self.model.calculation_groups:
            if not group.items:
                continue

            names = [item.name for item in group.items]
            out.append(
                Requirement(
                    id=f"REQ-B-{_identity('calculation_group', group.table)}",
                    kind=Kind.BUSINESS,
                    category="Metrics and KPIs",
                    statement=(
                        f"Users shall be able to view any reported metric through the "
                        f"**{group.table}** calculation group, which offers "
                        f"{_join(f'**{n}**' for n in names)}."
                    ),
                    rationale=(
                        f"The model defines a calculation group on {group.table} with "
                        f"{len(names)} item(s). Selecting one substitutes its own "
                        f"expression around whichever measure is on the report, so "
                        f"these are analytical options offered to every metric rather "
                        f"than metrics in their own right."
                    ),
                    confidence=Confidence.HIGH,
                    evidence=tuple(
                        Evidence(
                            node_id=calculation_item_id(group.table, item.name),
                            fingerprint=item.fingerprint,
                            detail=item.expression,
                        )
                        for item in group.items
                    ),
                )
            )

            for item in group.items:
                if not item.expression:
                    continue
                out.append(
                    Requirement(
                        id=f"REQ-F-{_identity('calculation_item', group.table, item.name)}",
                        kind=Kind.FUNCTIONAL,
                        category="Calculation groups",
                        statement=(
                            f"Selecting **{item.name}** from **{group.table}** shall "
                            f"evaluate the active measure through the DAX expression "
                            f"recorded in the evidence for this requirement."
                        ),
                        rationale=(
                            "The item replaces the measure's own evaluation wherever it "
                            "is applied, so the figure a report shows is this expression "
                            "wrapped around the measure -- not the measure's DAX alone."
                        ),
                        confidence=Confidence.HIGH,
                        evidence=(
                            Evidence(
                                node_id=calculation_item_id(group.table, item.name),
                                fingerprint=item.fingerprint,
                                detail=item.expression,
                            ),
                        ),
                    )
                )

        return out

    # -- overall shape ---------------------------------------------------------

    def _from_model_shape(self) -> list[Requirement]:
        """Requirements about the solution as a whole, not any single object."""
        out: list[Requirement] = []
        user_tables = [t for t in self.model.user_tables() if not t.is_measure_only]

        if user_tables:
            names = sorted(t.name for t in user_tables)
            out.append(
                Requirement(
                    # No model.name here: it is the file or folder Concordance
                    # happened to read the model from, not part of the model's
                    # own identity. Exporting the same dataset to a differently
                    # named .pbix, or cloning a TMDL folder under a new name,
                    # changes neither its tables nor what it means -- but it
                    # used to change this id anyway, which orphans any record
                    # tied to the old one. There is exactly one Scope
                    # requirement per model, so no discriminator is needed.
                    id=f"REQ-B-{_identity('scope')}",
                    kind=Kind.BUSINESS,
                    category="Scope",
                    statement=(
                        f"The solution shall cover {len(names)} "
                        f"subject area{'s' if len(names) != 1 else ''}: "
                        f"{_join(f'**{n}**' for n in names)}."
                    ),
                    rationale=(
                        "These are the data-bearing tables in the model, so they define "
                        "the boundary of what the solution reports on."
                    ),
                    confidence=Confidence.HIGH,
                    evidence=tuple(
                        Evidence(
                            node_id=table_id(t.name),
                            fingerprint=t.fingerprint,
                            detail=f"table {t.name}",
                        )
                        for t in user_tables
                    ),
                )
            )

        sourced = [t for t in self.model.tables if t.power_query and not t.is_system]
        if sourced:
            out.append(
                Requirement(
                    # Same reasoning as the Scope requirement above: singleton
                    # per model, so the file name buys no uniqueness and only
                    # costs stability.
                    id=f"REQ-F-{_identity('ingest')}",
                    kind=Kind.FUNCTIONAL,
                    category="Data acquisition",
                    statement=(
                        f"{len(sourced)} tables shall be populated by their recorded "
                        f"Power Query (M) transformations, which define the source "
                        f"system, filtering and shaping applied before load."
                    ),
                    rationale=(
                        "The M queries are the contract with upstream systems; a change "
                        "there changes what data reaches every downstream metric."
                    ),
                    confidence=Confidence.HIGH,
                    evidence=tuple(
                        Evidence(
                            node_id=table_id(t.name),
                            fingerprint=t.fingerprint,
                            detail=f"Power Query defined for {t.name}",
                        )
                        for t in sorted(sourced, key=lambda t: t.name)
                    ),
                )
            )

        # Anything the extractor could not cover is stated as a documented gap
        # rather than left for a reader to assume was absent.
        for gap in self.model.coverage_gaps:
            out.append(
                Requirement(
                    # gap.feature already discriminates among the gaps in one
                    # model; model.name added nothing but a dependency on the
                    # file it was read from.
                    id=f"REQ-F-{_identity('gap', gap.feature)}",
                    kind=Kind.FUNCTIONAL,
                    category="Documented gaps",
                    statement=(
                        f"This model contains {gap.count} {gap.feature} which are **not "
                        f"covered by this document** and require separate specification."
                    ),
                    rationale=(
                        "The extractor detected these objects but does not yet read their "
                        "definitions. Recording the gap prevents the document from being "
                        "mistaken for a complete specification."
                    ),
                    confidence=Confidence.LOW,
                    evidence=(),
                )
            )

        return out


def _join(items) -> str:
    """Human list joining: 'a', 'a and b', 'a, b and c'."""
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])} and {items[-1]}"

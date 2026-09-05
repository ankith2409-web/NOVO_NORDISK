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
from concordance.generate import patterns, phrasing
from concordance.graph.csg import (
    SemanticGraph,
    calculation_item_id,
    column_id,
    hierarchy_id,
    kpi_id,
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


class Corroboration(Enum):
    """Whether anything in the file shows this metric actually being used.

    A second axis, and the reason it exists is a fair criticism of the first
    one: `Confidence` says where a statement came from, never whether the
    thing it describes is good business logic. A measure left behind by a
    developer is *stated by the model* exactly as loudly as the company's
    headline KPI, so both were being asserted at HIGH confidence -- and a
    reader could not tell them apart.

    Confidence is not the place to fix that. Downgrading a real, declared
    measure to "medium" would be a lie about provenance to smuggle in a hint
    about quality. So quality-of-evidence gets its own axis, computed from
    facts the file carries: is the metric shown on a report page, or read by
    another measure, or neither?

    None of these is a verdict. A metric nothing corroborates may be brand
    new, or consumed by something outside this file. It is flagged as
    *uncorroborated*, which is a question for a person, not a defect.
    """

    #: A report tile shows it. The strongest evidence a file can carry.
    SHOWN_ON_REPORT = "shown_on_report"
    #: Another measure reads it, so it is a building block.
    READ_BY_A_MEASURE = "read_by_a_measure"
    #: Neither. Worth a human's attention before this is signed off.
    NOTHING_IN_THIS_FILE = "nothing_in_this_file"
    #: The requirement is not about a metric, so the question does not arise.
    NOT_A_METRIC = "not_a_metric"


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
    #: Whether anything in the file shows this metric in use. See `Corroboration`.
    corroboration: Corroboration = Corroboration.NOT_A_METRIC
    #: What the reader should know about that, in words. Empty when there is
    #: nothing to qualify.
    caveat: str = ""

    @property
    def uncorroborated(self) -> bool:
        """True for a metric nothing in this file shows being used."""
        return self.corroboration is Corroboration.NOTHING_IN_THIS_FILE

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
        # Every measure a report tile projects, by name. A `.SemanticModel`
        # folder carries no report, so this is empty there and corroboration
        # falls back to the dependency graph alone -- which is the honest
        # outcome: the absence of a report is not evidence of disuse.
        self._on_report = {
            field.name
            for visual in self.model.visuals()
            for field in visual.fields
        }
        # Which pages, not just whether. A business reader's first question
        # about a metric is where they would see it, and the answer is in the
        # file -- it was simply not being carried into the document.
        self._pages_showing: dict[str, list[str]] = {}
        for visual in self.model.visuals():
            page = getattr(visual, "page", "") or ""
            for field in visual.fields:
                seen = self._pages_showing.setdefault(field.name, [])
                if page and page not in seen:
                    seen.append(page)

    def _why(self, measure) -> str:
        """Why this metric is in the document, in terms a reader can act on.

        This used to read "A measure named Analysis DAX[Net Sales] is defined
        in the model, so the business tracks this quantity" -- for every metric,
        identically, fifty times in one document. It is circular (the metric is
        here because it is here), it is written in the file's vocabulary rather
        than the reader's, and a paragraph repeated fifty times is a paragraph
        nobody reads by the third.

        So it says whichever of these is true, in order of what a business
        reader actually asks: what the author said it was for, then where it is
        seen, then what depends on it. Where none of those is known the line is
        omitted entirely rather than padded -- silence is honest, and a
        tautology is not.
        """
        described = (getattr(measure, "description", "") or "").strip()
        if described:
            return f"The model's author describes it as: {described}"

        pages = self._pages_showing.get(measure.name) or []
        if pages:
            where = _join(pages[:4]) + (" and elsewhere" if len(pages) > 4 else "")
            page_word = "page" if len(pages) == 1 else "pages"
            return f"It is shown on the {where} {page_word} of this report."

        readers = self.graph.dependents_of(measure_id(measure.table, measure.name))
        if readers:
            count = len(readers)
            noun = "metric" if count == 1 else "metrics"
            return f"{count} other {noun} in this model are calculated from it."
        return ""

    def _corroborate(self, measure) -> tuple["Corroboration", str]:
        """Whether anything in this file shows a measure being used."""
        if measure.name in self._on_report:
            return (
                Corroboration.SHOWN_ON_REPORT,
                "",
            )
        if self.graph.dependents_of(measure_id(measure.table, measure.name)):
            return (Corroboration.READ_BY_A_MEASURE, "")

        has_report = bool(self._on_report)
        where = (
            "no report tile in this file shows it and no other measure reads it"
            if has_report
            else "no other measure reads it, and this source carries no report layer "
            "to check against"
        )
        return (
            Corroboration.NOTHING_IN_THIS_FILE,
            f"Nothing in this file shows this metric in use: {where}. It may be new, "
            "consumed by something outside this file, or left behind -- the model "
            "cannot tell those apart, so a person should.",
        )

    def derive(self) -> list[Requirement]:
        out: list[Requirement] = []
        out.extend(self._from_measures())
        out.extend(self._from_relationships())
        out.extend(self._from_hierarchies())
        out.extend(self._from_calculated_columns())
        out.extend(self._from_roles())
        out.extend(self._from_object_permissions())
        out.extend(self._from_perspectives())
        out.extend(self._from_variations())
        out.extend(self._from_kpis())
        out.extend(self._from_calculation_groups())
        out.extend(self._from_load_steps())
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

            corroboration, caveat = self._corroborate(measure)
            detected = patterns.detect(measure.expression)
            behaviour = detected[0] if detected else None

            columns = sorted(
                f"{table}[{column}]" for table, column in measure.depends_on_columns
            )
            upstream = sorted(measure.depends_on_measures)

            # BUSINESS: what the metric is, in plain terms. Phrased without an
            # article so every pattern label reads correctly -- "a ranking" and
            # "a conditional logic" cannot share a sentence frame.
            # The business document gets the plain sentence and the functional
            # one keeps the precise clause. Same detected behaviour, two
            # readers -- and the technical wording in a BRD reads as jargon
            # explained with jargon, fifty times over.
            descriptor = (
                f" {behaviour.plain}"
                if behaviour and behaviour.plain
                else f" It applies {behaviour.label}, which {behaviour.description}."
                if behaviour
                else ""
            )
            source = ""
            if columns:
                source = f" It is calculated from {_join(_plain(c) for c in columns)}."
            elif upstream:
                source = f" It is built from {_join(upstream)}."

            out.append(
                Requirement(
                    id=f"REQ-B-{_identity('measure', measure.table, measure.name)}",
                    kind=Kind.BUSINESS,
                    category="Metrics and KPIs",
                    # "shall report X" rather than "shall report X as a
                    # reportable metric", which said the same thing twice and
                    # said it fifty times over.
                    statement=(
                        f"The solution shall report **{measure.name}**."
                        f"{descriptor}{source}"
                    ),
                    rationale=self._why(measure),
                    confidence=Confidence.HIGH,
                    evidence=evidence,
                    corroboration=corroboration,
                    caveat=caveat,
                )
            )

            # FUNCTIONAL: exactly how it must be computed.
            dependency_note = ""
            if upstream:
                noun = "measure" if len(upstream) == 1 else "measures"
                dependency_note = (
                    f" It depends on the {noun} {_join(f'[{m}]' for m in upstream)}, "
                    f"which must be evaluated first."
                )
            out.append(
                Requirement(
                    id=f"REQ-F-{_identity('measure', measure.table, measure.name)}",
                    kind=Kind.FUNCTIONAL,
                    category="Measure definitions",
                    # Built from what was detected rather than from one fixed
                    # frame. The old sentence was identical for every measure in
                    # a model -- 58 times over on Sales_Returns_Sample -- which
                    # is a document a reviewer stops reading, and a skimmed
                    # specification is the failure this project exists to
                    # prevent. Still derived, still deterministic: see
                    # generate/phrasing.py.
                    statement=(
                        phrasing.functional_statement(
                            measure.qualified_name,
                            detected,
                            patterns.aggregation_of(measure.expression),
                            columns,
                            upstream,
                        )
                        + phrasing.secondary_note(detected)
                        + dependency_note
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

    # -- KPIs, object security, perspectives ------------------------------------

    def _from_kpis(self) -> list[Requirement]:
        """What the business considers a good number, which the measure never says."""
        out: list[Requirement] = []

        for kpi in self.model.kpis:
            evidence = (
                Evidence(
                    node_id=kpi_id(kpi.table, kpi.measure),
                    fingerprint=kpi.fingerprint,
                    detail="; ".join(
                        f"{label}: {text}"
                        for label, text in (
                            ("target", kpi.target_expression),
                            ("status", kpi.status_expression),
                            ("trend", kpi.trend_expression),
                        )
                        if text
                    ),
                ),
            )
            ident = _identity("kpi", kpi.table, kpi.measure)
            against = (
                f" against {kpi.target_description}"
                if kpi.target_description
                else " against the target recorded in the evidence"
            )

            out.append(
                Requirement(
                    id=f"REQ-B-{ident}",
                    kind=Kind.BUSINESS,
                    category="Metrics and KPIs",
                    statement=(
                        f"**{kpi.measure}** shall be tracked as a key performance "
                        f"indicator{against}, with its status reported against the "
                        f"thresholds the model defines."
                    ),
                    rationale=(
                        f"The model attaches a KPI to {kpi.qualified_name}, so the "
                        f"business does not merely report this figure -- it judges it."
                    ),
                    confidence=Confidence.HIGH,
                    evidence=evidence,
                )
            )

            if kpi.status_expression:
                out.append(
                    Requirement(
                        id=f"REQ-F-{ident}",
                        kind=Kind.FUNCTIONAL,
                        category="KPI thresholds",
                        statement=(
                            f"The status shown for **{kpi.qualified_name}** shall be "
                            f"computed by the DAX expression recorded in the evidence "
                            f"for this requirement, evaluated against its target."
                        ),
                        rationale=(
                            "The threshold expression decides which figures are "
                            "reported as acceptable. Changing it changes that verdict "
                            "without changing the measure, so it is a requirement in "
                            "its own right rather than a presentation detail."
                        ),
                        confidence=Confidence.HIGH,
                        evidence=evidence,
                    )
                )

        return out

    def _from_object_permissions(self) -> list[Requirement]:
        """Fields a role cannot see at all -- distinct from seeing fewer rows."""
        out: list[Requirement] = []
        hidden: dict[str, list[str]] = {}
        for permission in self.model.object_permissions:
            if permission.hides:
                hidden.setdefault(permission.role, []).append(permission.target)

        for role, targets in sorted(hidden.items()):
            out.append(
                Requirement(
                    id=f"REQ-B-{_identity('ols', role)}",
                    kind=Kind.BUSINESS,
                    category="Access and visibility",
                    statement=(
                        f"Users assigned the **{role}** role shall not have access to "
                        f"{_join(f'**{t}**' for t in sorted(targets))}."
                    ),
                    rationale=(
                        "Object-level security hides these entirely rather than "
                        "filtering their rows, so a member of this role cannot see "
                        "that the field exists, let alone its values."
                    ),
                    confidence=Confidence.HIGH,
                    evidence=tuple(
                        Evidence(
                            node_id=role_id(role),
                            fingerprint=fingerprint_parts("ols", role, target),
                            detail=f"{target}: no access",
                        )
                        for target in sorted(targets)
                    ),
                )
            )
        return out

    def _from_variations(self) -> list[Requirement]:
        """What drilling a column actually does, when it is not the obvious thing.

        Almost every variation in a real model points at one of Power BI's
        auto-generated `LocalDateTable_<guid>` hierarchies -- its automatic
        date plumbing. Printing that name would put a GUID into a business
        document and describe a table this tool deliberately hides everywhere
        else, so those are stated for what they are instead.
        """
        out: list[Requirement] = []

        for variation in sorted(
            self.model.variations, key=lambda v: (v.table, v.column, v.name)
        ):
            target = variation.default_hierarchy or ""
            owner = target.split("[")[0].split(".")[0].strip("' ")
            automatic = not target or owner in self._system_tables

            if automatic:
                statement = (
                    f"Users shall be able to drill **{variation.qualified_name}** "
                    f"through Power BI's automatic date hierarchy."
                )
                rationale = (
                    "The column carries a variation pointing at a generated date "
                    "hierarchy, which Power BI creates and maintains itself. Named "
                    "rather than reproduced: the generated table's identifier is not "
                    "something anyone can act on."
                )
            else:
                statement = (
                    f"Drilling **{variation.qualified_name}** shall navigate "
                    f"**{target}** rather than the column's own values."
                )
                rationale = (
                    "The column defines a variation, so expanding it walks a "
                    "different hierarchy than its own field suggests -- behaviour "
                    "nothing in the column's definition reveals."
                )

            out.append(
                Requirement(
                    id=f"REQ-B-{_identity('variation', variation.table, variation.column, variation.name)}",
                    kind=Kind.BUSINESS,
                    category="Navigation and drill paths",
                    statement=statement,
                    rationale=rationale,
                    confidence=Confidence.HIGH,
                    evidence=(
                        Evidence(
                            node_id=column_id(variation.table, variation.column),
                            fingerprint=fingerprint_parts(
                                "variation", variation.table, variation.column,
                                variation.name, target,
                            ),
                            detail=(
                                f"variation {variation.name}"
                                + (f" -> {target}" if target else "")
                            ),
                        ),
                    ),
                )
            )
        return out

    def _from_perspectives(self) -> list[Requirement]:
        """What each audience is actually shown."""
        out: list[Requirement] = []

        for perspective in self.model.perspectives:
            if not perspective.members:
                continue
            measures = [m.name for m in perspective.members if m.object_kind == "Measure"]
            out.append(
                Requirement(
                    id=f"REQ-B-{_identity('perspective', perspective.name)}",
                    kind=Kind.BUSINESS,
                    category="Access and visibility",
                    statement=(
                        f"The **{perspective.name}** view shall expose "
                        f"{_join(f'**{t}**' for t in perspective.tables)}"
                        + (
                            f", offering {_join(f'**{m}**' for m in sorted(measures))}."
                            if measures
                            else "."
                        )
                    ),
                    rationale=(
                        f"The model defines a perspective named {perspective.name} "
                        f"covering {len(perspective.members)} object(s). A perspective "
                        f"is a scope statement: this audience is given this subset, not "
                        f"the whole model."
                    ),
                    confidence=Confidence.HIGH,
                    evidence=(
                        Evidence(
                            node_id=f"perspective:{perspective.name}",
                            fingerprint=perspective.fingerprint,
                            detail=", ".join(
                                f"{m.object_kind} {m.table}[{m.name}]"
                                if m.object_kind != "Table"
                                else f"Table {m.name}"
                                for m in perspective.members
                            ),
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

    # -- how each table is loaded ----------------------------------------------

    def _from_load_steps(self) -> list[Requirement]:
        """What the Power Query does between the source and the table.

        The finding worth surfacing is narrower than "here are the steps": some
        steps change how many rows survive. A table loaded from a file and then
        filtered is not that file, and anyone reconciling a figure against the
        source directly will get a different number with nothing in the
        document to explain the difference. Naming those steps specifically is
        the difference between a list and a warning.
        """
        from concordance.normalize.mquery import extract_steps

        out: list[Requirement] = []

        for table in sorted(self.model.user_tables(), key=lambda t: t.name):
            if not table.power_query:
                continue
            steps = extract_steps(table.power_query)
            evidence = (
                Evidence(
                    node_id=table_id(table.name),
                    fingerprint=table.fingerprint,
                    detail=table.power_query.strip(),
                ),
            )
            reshaping = [s for s in steps if s.changes_row_count]

            # Effect first, step name in brackets: the sentence is read for what
            # the query does, and the step names are the reference someone needs
            # only once they open the query itself.
            described = [f"{s.effect} (**{s.name}**)" for s in steps if s.effect]
            unknown = [s for s in steps if not s.effect]
            listing = _join(described) if described else ""
            note = ""
            if unknown:
                # Named rather than omitted: a step this module cannot describe
                # is still a step that runs, and silently listing only the
                # recognised ones would misrepresent the query as shorter than
                # it is.
                note = (
                    f" {len(unknown)} further step(s) apply functions this tool does "
                    f"not describe ({_join(sorted({s.function or s.name for s in unknown}))}); "
                    f"the query itself is recorded in the evidence."
                )

            if steps:
                statement = (
                    f"**{table.name}** shall be populated by the {len(steps)}-step "
                    f"Power Query recorded in the evidence for this requirement"
                    + (f", which in order {listing}" if listing else "")
                    + f".{note}"
                )
            else:
                # Legal M: a query can be one expression with no `let` block.
                # Still worth a requirement, so every table loaded from
                # somewhere has exactly one statement covering how.
                statement = (
                    f"**{table.name}** shall be populated by the single-expression "
                    f"Power Query recorded in the evidence for this requirement."
                )
            if reshaping:
                statement += (
                    f" Because {_join(f'**{s.name}**' for s in reshaping)} changes "
                    f"how many rows survive, this table is not a row-for-row copy of "
                    f"its source, and figures reconciled directly against that source "
                    f"are not required to match."
                )

            out.append(
                Requirement(
                    id=f"REQ-F-{_identity('load', table.name)}",
                    kind=Kind.FUNCTIONAL,
                    category="Data acquisition",
                    statement=statement,
                    rationale=(
                        "The M query is the contract with the upstream system: it "
                        "determines which rows and columns reach the model, so a "
                        "change to it changes every metric computed over this table."
                    ),
                    confidence=Confidence.HIGH,
                    evidence=evidence,
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

        # The model-wide "N tables are populated by their Power Query" statement
        # that used to live here is gone. It claimed the M "defines the source
        # system, filtering and shaping applied before load" while reading none
        # of it, and `_from_load_steps` now says that per table from the steps
        # actually parsed -- a summary asserting what a real requirement states
        # is duplication in the best case and a stale claim in the worst.

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


def _plain(reference: str) -> str:
    """`Calendar[Date]` as "Date (Calendar)".

    Square brackets are DAX. They belong in the functional document, where the
    reader is writing the formula, and not in the business one, where they are
    punctuation nobody has been taught.
    """
    if "[" in reference and reference.endswith("]"):
        table, column = reference[:-1].split("[", 1)
        return f"{column} ({table})"
    return reference


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

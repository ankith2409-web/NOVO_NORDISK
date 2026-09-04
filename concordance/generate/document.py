"""Assembling requirements into a BRD and an FRD.

The document is a view over derived requirements, not a separate artefact: it
adds ordering, sectioning and front matter, and renders a traceability matrix
that binds every requirement to the fingerprint it was verified against.

Two things travel with the document rather than being reported alongside it:
requirements that need human confirmation are marked in place, and features the
extractor could not read are stated as documented gaps. A specification that
looks complete but silently is not is worse than one that admits its edges.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field, replace
from datetime import date

from concordance.generate.requirements import (
    Confidence,
    Kind,
    Requirement,
    RequirementDeriver,
)
from concordance.graph.csg import SemanticGraph


@dataclass(frozen=True)
class Section:
    title: str
    requirements: tuple[Requirement, ...]


@dataclass(frozen=True)
class Changed:
    """One difference from the previous version, in words rather than jargon.

    Deliberately not the ``Change`` the drift engine produces. That one carries
    fingerprints, node ids and an enum, which are exactly right on a screen
    built for them and wrong in a document a business reader signs. This holds
    the same fact said plainly, and is built from that one so there is no second
    comparison to keep true.
    """

    #: "measure Adverse Event Rate" -- what the change is to.
    what: str
    #: added / removed / changed / renamed.
    change: str
    #: A sentence saying what it means for whoever reads this document.
    means: str
    #: The requirement ids this change lands on, if any.
    affects: tuple[str, ...] = ()


@dataclass(frozen=True)
class Document:
    """A BRD or FRD, ready to render."""

    title: str
    kind: Kind
    model_name: str
    source: str
    generated_on: str
    sections: tuple[Section, ...]
    #: Measure name -> its SQL translation, when the document was built with a
    #: grain. Held here rather than looked up while rendering so that both
    #: renderers agree, and so a document can be inspected without a model.
    sql: dict[str, object] = field(default_factory=dict)
    #: The SQL join for each relationship, keyed by ``From[Col]->To[Col]``.
    #: Alongside the measure SQL because a retrieval system handed this document
    #: needs both: a query that joins two tables is unusable to an agent that
    #: was never told how those tables relate.
    joins: dict[str, str] = field(default_factory=dict)
    #: The grain those translations were rendered at. Meaningless to show SQL
    #: without it: the same measure at a different grain is a different query.
    sql_grain: tuple[str, ...] = ()
    sql_dialect: str = "duckdb"
    #: Subject areas the document covers, for the scope section every real BRD
    #: and FRD opens with.
    subject_areas: tuple[str, ...] = ()
    #: What the extractor could not read, as (feature, count, reason). These are
    #: the document's constraints, and they are real ones: a template's
    #: "Assumptions and Constraints" section is usually filled with generalities,
    #: and this one is filled with the specific things this document does not
    #: know about the model it describes.
    limits: tuple[tuple[str, int, str], ...] = ()
    #: Business terms and what they mean, from the model's own descriptions.
    glossary: tuple[tuple[str, str], ...] = ()
    #: What moved since the version this model was compared against, if one was
    #: given. In the document as well as on the Drift tab, and for a reason a
    #: reviewer gave plainly: the document is what gets sent to someone, and
    #: "what changed" is the first thing they ask. A separate screen answers it
    #: only for whoever is sitting in front of the tool.
    changes: tuple[Changed, ...] = ()
    #: The version this was compared against, named as the document says it.
    changed_from: str = ""

    @property
    def requirements(self) -> tuple[Requirement, ...]:
        return tuple(r for s in self.sections for r in s.requirements)

    @property
    def review_queue(self) -> tuple[Requirement, ...]:
        return tuple(r for r in self.requirements if r.needs_review)

    def counts(self) -> dict[str, int]:
        reqs = self.requirements
        return {
            "requirements": len(reqs),
            "high": sum(1 for r in reqs if r.confidence is Confidence.HIGH),
            "medium": sum(1 for r in reqs if r.confidence is Confidence.MEDIUM),
            "low": sum(1 for r in reqs if r.confidence is Confidence.LOW),
            "needs_review": len(self.review_queue),
            # Metrics nothing in the file shows in use. A separate axis from
            # confidence: see `requirements.Corroboration`.
            "uncorroborated": sum(1 for r in reqs if r.uncorroborated),
        }


#: Section order is editorial: scope first, then what the business measures,
#: then how it navigates, then the documented edges of the specification.
_BUSINESS_ORDER = [
    "Scope",
    "Metrics and KPIs",
    "Dimensional analysis",
    "Drill-down and navigation",
    "Documented gaps",
]
_FUNCTIONAL_ORDER = [
    "Data acquisition",
    "Data model relationships",
    "Measure definitions",
    "Derived attributes",
    "Hierarchy definitions",
    # Objects the model declares but leaves undefined, and features the
    # extractor could not read. Both belong at the end, where a reader looking
    # for the edges of the specification will find them together.
    "Incomplete definitions",
    "Documented gaps",
]


def build(
    graph: SemanticGraph,
    kind: Kind,
    generated_on: str | None = None,
    sql_grain: tuple[str, ...] | None = None,
    sql_dialect: str = "duckdb",
    drift=None,
) -> Document:
    """Assemble one document from a model's derived requirements.

    Passing ``sql_grain`` renders each measure's SQL alongside its DAX. It is
    off by default because SQL without a stated grain would be a claim the
    document cannot support -- and because a BRD describes what the business
    needs, not how a query would express it.

    Passing ``drift`` -- a ``DriftReport`` against the previous version -- adds
    a "What changed since" section. Both documents carry it, unlike the SQL:
    a BRD reader has no use for a query and every use for knowing that the
    definition behind a number they signed off has moved.
    """
    requirements = [r for r in RequirementDeriver(graph).derive() if r.kind is kind]
    order = _BUSINESS_ORDER if kind is Kind.BUSINESS else _FUNCTIONAL_ORDER

    by_category: dict[str, list[Requirement]] = {}
    for requirement in requirements:
        by_category.setdefault(requirement.category, []).append(requirement)

    sections = [
        Section(title=category, requirements=tuple(by_category[category]))
        for category in order
        if category in by_category
    ]
    # Any category not in the fixed order still appears, after the known ones.
    for category in sorted(set(by_category) - set(order)):
        sections.append(Section(title=category, requirements=tuple(by_category[category])))

    label = (
        "Business Requirements Document"
        if kind is Kind.BUSINESS
        else "Functional Requirements Document"
    )
    return Document(
        title=f"{label} — {graph.model.name.replace('_', ' ')}",
        kind=kind,
        model_name=graph.model.name,
        source=graph.model.source_path,
        generated_on=generated_on or date.today().isoformat(),
        sections=tuple(sections),
        sql=_translations(graph, sql_grain, sql_dialect)
        if sql_grain is not None and kind is Kind.FUNCTIONAL
        else {},
        joins=_joins(graph, sql_dialect)
        if sql_grain is not None and kind is Kind.FUNCTIONAL
        else {},
        sql_grain=tuple(sql_grain or ()),
        sql_dialect=sql_dialect,
        subject_areas=tuple(sorted(t.name for t in graph.model.user_tables())),
        limits=tuple(
            (gap.feature, gap.count, gap.reason) for gap in graph.model.coverage_gaps
        ),
        glossary=_glossary(graph),
        # Filtered to the ids this document actually contains: a reader of the
        # FRD following a reference to REQ-B-6f765c would be looking for a
        # requirement that is not in the document in their hand.
        changes=_changes(drift, {r.id for s in sections for r in s.requirements})
        if drift is not None
        else (),
        changed_from=getattr(drift, "before_label", "") if drift is not None else "",
    )


#: What each kind of change means for someone reading the document, rather than
#: for someone reading a diff. Written as the consequence, because that is the
#: only part a reader can act on: "changed" is not news, "the number this
#: reports is not the number the last version reported" is.
#:
#: Said once per group rather than once per row. The same sentence repeated
#: down a table column is the thing that makes generated documents unreadable,
#: and it carries no more information the fourth time than the first.
_MEANS = {
    "changed": "Edited, so each of these can now report a different number than the "
    "previous version reported. These are the ones to check against what was agreed.",
    "removed": "In the previous version and gone from this one. Anything that relied on "
    "these no longer has them.",
    "added": "New in this version. Nothing in the previous document describes these.",
    "renamed": "Only the name changed. These calculate exactly what they calculated "
    "before -- the fingerprints prove it -- so nothing here needs re-checking, but "
    "anything referring to the old name needs updating.",
}

#: The heading each group gets, and the order they appear in: what a reader has
#: to act on first, the provably-harmless renames last.
_CHANGE_GROUPS = (
    ("changed", "Changed definitions"),
    ("removed", "Removed"),
    ("added", "Added"),
    ("renamed", "Renamed only"),
)


def _changes(report, ours: set[str] | None = None) -> tuple[Changed, ...]:
    """The drift report, said in words a business reader can act on."""
    # Which requirements each change lands on. Built from the report's own
    # mapping rather than recomputed: the binding from evidence to change is
    # the whole basis of the claim, and a second derivation of it could differ.
    lands_on: dict[str, list[str]] = {}
    for affected in getattr(report, "affected", ()):
        for change in affected.changes:
            lands_on.setdefault(change.node_id, []).append(affected.id)

    rows: list[Changed] = []
    for change in getattr(report, "changes", ()):
        name = change.node_id.split(":", 1)[-1]
        kind = change.kind.value
        what = f"{change.object_kind} {name}"
        if kind == "renamed" and change.before is not None:
            was = change.before.node_id.split(":", 1)[-1]
            what = f"{change.object_kind} {name} (was {was})"
        rows.append(
            Changed(
                what=what,
                change=kind,
                means=_MEANS.get(kind, ""),
                affects=tuple(
                    sorted(
                        id_
                        for id_ in set(lands_on.get(change.node_id, ()))
                        if ours is None or id_ in ours
                    )
                ),
            )
        )
    # Grouped by what the reader has to do about it: the ones that can have
    # moved a number first, the provably-harmless renames last.
    order = {"changed": 0, "removed": 1, "added": 2, "renamed": 3}
    return tuple(sorted(rows, key=lambda r: (order.get(r.change, 9), r.what)))


def _glossary(graph: SemanticGraph) -> tuple[tuple[str, str], ...]:
    """Terms the model itself defines, for the glossary every template has.

    Taken from descriptions the modeller wrote, never invented. A glossary of
    terms this document made up would be worse than no glossary, and an empty
    one is an honest signal that the model carries no descriptions -- which is
    itself worth knowing about a model.
    """
    terms: dict[str, str] = {}
    for measure in graph.model.measures:
        if measure.description:
            terms[measure.name] = measure.description
    for table in graph.model.user_tables():
        description = getattr(table, "description", "")
        if description:
            terms[table.name] = description
    return tuple(sorted(terms.items()))


def _translations(graph: SemanticGraph, grain, dialect: str) -> dict[str, object]:
    """Every measure's SQL, keyed by measure name."""
    from concordance.generate.sql import to_dialect, translate_all

    out: dict[str, object] = {}
    for translation in translate_all(graph.model, tuple(grain or ())):
        out[translation.measure] = (
            translation
            if not translation.sql
            else replace(translation, sql=to_dialect(translation.sql, dialect))
        )
    return out


def _joins(graph: SemanticGraph, dialect: str) -> dict[str, str]:
    """Each relationship's SQL join, keyed the way a requirement names it."""
    from concordance.generate.sql import joins as sql_joins

    return {
        f"{j.from_table}[{j.from_column}]->{j.to_table}[{j.to_column}]": j.sql
        for j in sql_joins(graph.model, dialect)
    }


def join_of(requirement: Requirement) -> str:
    """The relationship key a requirement is about, or "".

    Relationship requirements carry their two tables as evidence rather than a
    single node id, so the key is rebuilt from the statement's own
    ``Table[Column]`` pair -- which is the same pair the requirement was
    generated from.
    """
    found = re.findall(r"`([^`\[]+)\[([^\]]+)\]`", requirement.statement)
    if len(found) != 2 or "relationship shall join" not in requirement.statement:
        return ""
    (lt, lc), (rt, rc) = found
    return f"{lt}[{lc}]->{rt}[{rc}]"


def measure_of(requirement: Requirement) -> str:
    """The measure a requirement is about, or "" when it is about something else.

    Evidence node ids are ``measure:Table[Name]``; the name is what the SQL
    translations are keyed by.
    """
    for evidence in requirement.evidence:
        node = evidence.node_id
        if node.startswith("measure:") and node.endswith("]") and "[" in node:
            return node[node.index("[") + 1 : -1]
    return ""


def to_markdown(document: Document) -> str:
    """Render as Markdown, including the traceability matrix."""
    counts = document.counts()
    lines: list[str] = []

    lines.append(f"# {document.title}")
    lines.append("")
    lines.append(f"**Source model:** `{document.source}`  ")
    lines.append(f"**Generated:** {document.generated_on}  ")
    lines.append(
        f"**Requirements:** {counts['requirements']} "
        f"({counts['high']} stated, {counts['medium']} inferred, {counts['low']} need confirmation)"
    )
    if counts.get("uncorroborated"):
        lines.append(
            f"**In use:** {counts['uncorroborated']} metric(s) below are not shown on "
            "any report page in this file and are read by no other measure  "
        )
    if document.sql:
        translated = sum(1 for t in document.sql.values() if getattr(t, "sql", ""))
        grain = ", ".join(f"`{g}`" for g in document.sql_grain) or "the whole model"
        lines.append(
            f"**SQL:** {translated} of {len(document.sql)} measures rendered as "
            f"{document.sql_dialect} at one row per {grain}  "
        )
    lines.append("")
    lines.append(
        "> Every requirement below was derived from the implemented semantic model and is "
        "bound to the fingerprint of the object that satisfies it. Confidence reflects "
        "whether the model *states* the requirement or whether it was *inferred* from "
        "structure — inferred items are marked and must be confirmed by a human before "
        "this document is treated as approved."
    )
    lines.append("")

    lines.extend(_front_matter(document))
    # Before the requirements and after the front matter: someone re-reading a
    # document they have already read wants this and nothing else, and should
    # not have to scroll through 60 requirements to find out there are three.
    lines.extend(_changes_lines(document))

    if document.review_queue:
        lines.append("## Awaiting human confirmation")
        lines.append("")
        lines.append(
            f"{len(document.review_queue)} requirement(s) could not be established from "
            "the model alone:"
        )
        lines.append("")
        for requirement in document.review_queue:
            lines.append(f"- **{requirement.id}** — {_plain(requirement.statement)}")
        lines.append("")

    for index, section in enumerate(document.sections, start=1):
        lines.append(f"## {index}. {section.title}")
        lines.append("")
        for requirement in section.requirements:
            flag = " ⚠ *needs confirmation*" if requirement.needs_review else ""
            lines.append(f"### {requirement.id}{flag}")
            lines.append("")
            lines.append(requirement.statement)
            lines.append("")
            lines.append(f"*Rationale:* {requirement.rationale}")
            lines.append("")
            lines.append(f"*Confidence:* {requirement.confidence.value}")
            lines.append("")
            # Deliberately its own line rather than folded into the rationale.
            # Confidence says where the statement came from; this says whether
            # anything corroborates that the metric is actually used, and a
            # reader has to be able to see the two are different questions.
            if requirement.caveat:
                lines.append(f"*In use:* {requirement.caveat}")
                lines.append("")
            # Show the implementation only when a single piece of evidence *is*
            # the implementation. For a requirement spanning many objects, one
            # arbitrary detail line would misrepresent what it is bound to.
            #
            # And only in the FRD. The reviewer drew the line herself: "BRD is
            # basically the complete business information ... very plain English,
            # that is it, and FRD is where you get into the details." A BRD
            # carrying thirty lines of DAX is not a business document, and the
            # person it is written for cannot read it. The statement and the
            # rationale say the same thing in words; the traceability matrix
            # still binds each one to the object it came from, so nothing is
            # lost -- it moves.
            if document.kind is not Kind.FUNCTIONAL:
                pass
            elif len(requirement.evidence) == 1 and requirement.evidence[0].detail:
                detail = requirement.evidence[0].detail
                if "\n" in detail:
                    lines.append("*Implementation:*")
                    lines.append("")
                    lines.append(f"```{_fence_language(requirement.evidence[0].node_id)}")
                    lines.append(detail)
                    lines.append("```")
                else:
                    lines.append(f"*Implementation:* `{detail}`")
                lines.append("")
            if len(requirement.evidence) > 1:
                lines.append(
                    f"*Bound to {len(requirement.evidence)} objects — see the "
                    f"traceability matrix.*"
                )
                lines.append("")

            # After the implementation, so the DAX and the SQL read as one pair.
            lines.extend(_sql_lines(document, requirement))
            lines.append("")

    lines.extend(_glossary_lines(document))

    lines.append("## Traceability matrix")
    lines.append("")
    lines.append("| Requirement | Bound to | Fingerprint | Confidence |")
    lines.append("|---|---|---|---|")
    for requirement in document.requirements:
        if requirement.evidence:
            first = requirement.evidence[0]
            target = first.node_id
            fingerprint = first.fingerprint[:12]
            if len(requirement.evidence) > 1:
                target += f" (+{len(requirement.evidence) - 1} more)"
        else:
            target = "—"
            fingerprint = "—"
        lines.append(
            f"| {requirement.id} | `{target}` | `{fingerprint}` | {requirement.confidence.value} |"
        )
    lines.append("")

    return "\n".join(lines)


#: What a BRD is expected to carry that no semantic model contains.
#:
#: Every published BRD template opens with objectives, stakeholders and a
#: cost-benefit case. None of the three is anywhere in a .pbix -- a model
#: records what is computed, never who asked for it or what it was worth. The
#: honest move is to name them as the document's own gaps rather than either
#: omitting them silently, which makes the document look complete when it is
#: not, or inventing them, which is the exact failure this whole project
#: exists to prevent.
#: What a complete BRD needs that no semantic model records.
#:
#: Named rather than left out, and named precisely: a reader who is told "the
#: audience is not recorded" can go and ask somebody, while a reader told
#: nothing assumes the question was considered and answered.
#:
#: Two of these are here because a reviewer asked for them by name, and both
#: are cases where the model carries a *partial* signal that must not be
#: mistaken for the whole answer. Perspectives and security roles say who
#: *may* see a metric, which is a permission and not a persona; and an
#: author's description is the only place intent is ever written down.
_NOT_IN_A_MODEL = (
    (
        "Business objectives and success measures",
        "why this reporting exists and what it is expected to change",
    ),
    (
        "Which audience each metric is for",
        "who actually reads it -- a regional manager, a finance team, an auditor. "
        "Where this model defines perspectives or security roles they are documented "
        "above, but those record who *may* see a metric, not who relies on it",
    ),
    (
        "Why each metric matters",
        "the business reason a quantity is worth tracking. The model records this "
        "only where an author wrote a description; everywhere else its existence is "
        "the only evidence of its importance, which is not evidence of importance",
    ),
    (
        "Stakeholders and approvers",
        "who owns each metric and who signs this document off",
    ),
    (
        "Cost, benefit and delivery schedule",
        "the commercial case and the dates it is judged against",
    ),
)


def front_matter_blocks(
    document: Document,
) -> list[tuple[str, list[str], list[str]]]:
    """The opening sections as (heading, paragraphs, bullets).

    Structured rather than pre-rendered because two renderers need it: Markdown
    wants strings with emphasis markers, Word wants paragraphs and list items.
    Letting each write its own wording would produce two documents that differ
    in the first thing anybody reads.
    """
    business = document.kind is not Kind.FUNCTIONAL
    blocks: list[tuple[str, list[str], list[str]]] = []

    if business:
        purpose = (
            f"This document states, in business terms, what the {document.model_name} "
            f"reporting solution is required to deliver: the subject areas it covers "
            f"and the metrics it is expected to report. It is written for the people "
            f"who own and approve that reporting, and it deliberately carries no "
            f"formulas — the Functional Requirements Document is where each metric's "
            f"implementation is specified."
        )
    else:
        purpose = (
            f"This document specifies how the {document.model_name} semantic model "
            f"satisfies the business requirements: the tables it reads, how those "
            f"tables join, and the definition behind every metric, in DAX as "
            f"implemented and in SQL as it would be written against a warehouse. It is "
            f"written for whoever has to verify or rebuild the logic, and everything "
            f"needed to do that is in this one document by design."
        )
    blocks.append(("Purpose", [purpose], []))

    blocks.append((
        "Scope of this document",
        [
            "Everything below is derived from the semantic model. The subject areas in "
            "scope are stated as a requirement of their own, and bound to the tables "
            "that satisfy it.",
            "Out of scope: report-page layout, visual formatting and usage, which sit "
            "outside the semantic model and cannot be read from it.",
        ],
        [],
    ))

    constraints = [
        "This document was generated from the model itself, so it describes what is "
        "implemented rather than what was intended. Where the two differ, the model is "
        "what this reports.",
        "That cuts both ways, and it is worth stating plainly: a metric left behind by "
        "a developer is declared by the model exactly as clearly as the company's "
        "headline figure, so both are stated here with the same confidence. Confidence "
        "records where a statement came from, never whether the thing it describes is "
        "sound business logic. Where nothing in this file shows a metric being used -- "
        "no report tile displays it and no other measure reads it -- that is marked on "
        "the requirement itself, as a question for a person rather than a verdict.",
    ]
    bullets: list[str] = []
    if document.limits:
        constraints.append(
            "The following were present in the source and could not be read, so no "
            "requirement below covers them:"
        )
        bullets = [f"{f} ({n}) — {why}" for f, n, why in document.limits]
    else:
        constraints.append(
            "Everything the extractor understands was read from this model, and no "
            "feature it recognises was skipped."
        )
    blocks.append(("Assumptions and constraints", constraints, bullets))

    if business:
        blocks.append((
            "To be supplied by the business",
            [
                "A semantic model records what is calculated. It does not record why "
                "anyone wanted it, who owns it, or what it was worth. These sections "
                "belong in a complete BRD and cannot be derived from the model, so they "
                "are named here rather than left out or invented:"
            ],
            [f"{heading} — {what}." for heading, what in _NOT_IN_A_MODEL],
        ))

    return blocks


def changes_intro(document: Document) -> list[str]:
    """The sentence or two that opens the "What changed" section.

    Shared by both renderers for the same reason the front matter is: this is
    the part a reader takes their impression from, and two renderers wording it
    differently would be two documents.
    """
    if not document.changed_from:
        return []
    moved = [c for c in document.changes if c.change != "renamed"]
    renames = len(document.changes) - len(moved)
    if not document.changes:
        return [
            f"Nothing. This model calculates exactly what {document.changed_from} "
            f"calculated. Formatting and comments were ignored when comparing, so this "
            f"is a statement about the logic, not about the file."
        ]

    def things(n: int) -> str:
        return "1 thing" if n == 1 else f"{n} things"

    if moved and renames:
        opening = (
            f"{things(len(moved))} changed in a way that affects what this model "
            f"contains or calculates, and {things(renames)} were renamed without their "
            f"logic changing."
        )
    elif moved:
        opening = (
            f"{things(len(moved))} changed in a way that affects what this model "
            f"contains or calculates."
        )
    else:
        opening = (
            f"{things(renames)} were renamed. Nothing calculates differently than it "
            f"did before."
        )
    lines = [f"Compared with {document.changed_from}: {opening}"]
    lines.append(
        "Each group below states what that kind of change means, then lists what it "
        "happened to and which requirements in this document it lands on."
    )
    return lines


def changes_groups(
    document: Document,
) -> list[tuple[str, str, list[Changed]]]:
    """The changes as (heading, what this group means, rows).

    Grouped rather than tabulated. A table with a "what it means" column repeats
    the same sentence on every row of the same kind, which is exactly the shape
    that makes a generated document unreadable; saying it once above four
    bullets says the same thing and can be skimmed.
    """
    groups = []
    for kind, heading in _CHANGE_GROUPS:
        rows = [c for c in document.changes if c.change == kind]
        if rows:
            groups.append((f"{heading} ({len(rows)})", _MEANS[kind], rows))
    return groups


def changes_line(row: Changed) -> str:
    """One change as a single line of plain text, shared by both renderers."""
    if row.affects:
        return f"{row.what} — affects {', '.join(row.affects)}"
    return row.what


def _changes_lines(document: Document) -> list[str]:
    """"What changed since the last version", as Markdown."""
    intro = changes_intro(document)
    if not intro:
        return []
    lines = [f"## What changed since {document.changed_from}", ""]
    for text in intro:
        lines.append(text)
        lines.append("")
    for heading, means, rows in changes_groups(document):
        lines.append(f"**{heading}.** {means}")
        lines.append("")
        for row in rows:
            lines.append(f"- `{row.what}`" + (
                f" — affects {', '.join(row.affects)}" if row.affects else ""
            ))
        lines.append("")
    return lines


def _front_matter(document: Document) -> list[str]:
    """The shared opening blocks, as Markdown."""
    lines: list[str] = []
    for heading, paragraphs, bullets in front_matter_blocks(document):
        lines.append(f"## {heading}")
        lines.append("")
        for text in paragraphs:
            lines.append(text)
            lines.append("")
        for text in bullets:
            lines.append(f"- {text}")
        if bullets:
            lines.append("")
    return lines


def _glossary_lines(document: Document) -> list[str]:
    """Terms the modeller described, rendered as the glossary a template ends with."""
    if not document.glossary:
        return []
    lines = ["## Glossary", ""]
    lines.append(
        "Taken from the descriptions recorded in the model. Terms with no description "
        "there do not appear here rather than being given one."
    )
    lines.append("")
    lines.append("| Term | Meaning |")
    lines.append("|---|---|")
    for term, meaning in document.glossary:
        lines.append(f"| **{term}** | {_plain(meaning)} |")
    lines.append("")
    return lines


def _sql_lines(document: Document, requirement: Requirement) -> list[str]:
    """The same measure expressed as SQL, directly beneath its DAX.

    Placed inline rather than gathered into an appendix so that a chunk of this
    document -- which is how it gets read once it is handed to a retrieval
    system -- carries the requirement, the DAX and the SQL together. An
    appendix would chunk into queries with nothing saying what they are for.
    """
    join = document.joins.get(join_of(requirement))
    if join:
        # A relationship requirement states the join in words; this states it in
        # the language the queries beside it are written in. Both, because the
        # document has two readers -- a person deciding whether the join is
        # right, and a retrieval system that needs it to answer with a query.
        return [
            f"*The same join in SQL* ({document.sql_dialect}):",
            "",
            "```sql",
            join,
            "```",
            "",
        ]

    translation = document.sql.get(measure_of(requirement))
    if translation is None:
        return []

    lines: list[str] = []
    sql = getattr(translation, "sql", "")
    if sql:
        lines.append(f"*Equivalent SQL* ({document.sql_dialect}):")
        lines.append("")
        lines.append("```sql")
        lines.append(sql)
        lines.append("```")
    else:
        # Stated, not omitted. A reader who finds SQL under fifteen measures
        # and nothing under the sixteenth would reasonably assume it was
        # missed; the reason is the point.
        # The reason is a sentence fragment ("X shifts the date filter
        # context"), so it is punctuated here rather than at its source, where
        # it is also read aloud by the interface without a trailing stop.
        reason = (getattr(translation, "reason", "") or "").rstrip(".")
        lines.append(
            f"*No SQL equivalent:* {reason}. This is a property of the "
            "expression rather than a gap in the translation — its value "
            "depends on filter context that a query cannot fix."
        )
    lines.append("")
    return lines


def _plain(text: str) -> str:
    """Strip the light Markdown emphasis used inside statements."""
    return text.replace("**", "").replace("`", "")


def _fence_language(node_id: str) -> str:
    """Which language a code block actually holds.

    Every fenced block used to be labelled ``dax``, including the ones holding
    a table's Power Query -- which is M, a different language with different
    keywords. A reader who trusts the label reads `let ... in` as DAX and finds
    nothing wrong with it, and any tooling that syntax-highlights on the fence
    marks half the document as broken DAX.

    A table's evidence is its M query; a measure's or a calculated column's is
    DAX. Nothing else in this document fences a multi-line expression.
    """
    return "m" if node_id.startswith("table:") else "dax"

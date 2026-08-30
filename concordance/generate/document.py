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
) -> Document:
    """Assemble one document from a model's derived requirements.

    Passing ``sql_grain`` renders each measure's SQL alongside its DAX. It is
    off by default because SQL without a stated grain would be a claim the
    document cannot support -- and because a BRD describes what the business
    needs, not how a query would express it.
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
    )


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
_NOT_IN_A_MODEL = (
    (
        "Business objectives and success measures",
        "why this reporting exists and what it is expected to change",
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
        "what this reports."
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

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

from dataclasses import dataclass
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


def build(graph: SemanticGraph, kind: Kind, generated_on: str | None = None) -> Document:
    """Assemble one document from a model's derived requirements."""
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
    )


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
    lines.append("")
    lines.append(
        "> Every requirement below was derived from the implemented semantic model and is "
        "bound to the fingerprint of the object that satisfies it. Confidence reflects "
        "whether the model *states* the requirement or whether it was *inferred* from "
        "structure — inferred items are marked and must be confirmed by a human before "
        "this document is treated as approved."
    )
    lines.append("")

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
            if len(requirement.evidence) == 1 and requirement.evidence[0].detail:
                detail = requirement.evidence[0].detail
                if "\n" in detail:
                    lines.append("*Implementation:*")
                    lines.append("")
                    lines.append("```dax")
                    lines.append(detail)
                    lines.append("```")
                else:
                    lines.append(f"*Implementation:* `{detail}`")
                lines.append("")
            elif len(requirement.evidence) > 1:
                lines.append(
                    f"*Bound to {len(requirement.evidence)} objects — see the "
                    f"traceability matrix.*"
                )
                lines.append("")
            lines.append("")

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


def _plain(text: str) -> str:
    """Strip the light Markdown emphasis used inside statements."""
    return text.replace("**", "").replace("`", "")

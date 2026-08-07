"""Comparing two snapshots, and saying which requirements are now in question.

This is where the fingerprint scheme pays off. Because a requirement is bound to
the fingerprint of the object that satisfies it, and because requirement ids are
derived from an object's *identity* rather than its content, a changed
implementation keeps its requirement id while moving its fingerprint. That makes
the useful sentence expressible: not "something changed somewhere", but
"REQ-F-b06dae is bound to a definition that no longer matches".

A reformatted expression produces the same fingerprint and is reported as
nothing at all. That silence is the point -- a drift report nobody trusts
because it cries wolf on whitespace is worse than no report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from concordance.drift.snapshot import ObjectRecord, Snapshot
from concordance.generate.requirements import Requirement, RequirementDeriver
from concordance.graph.csg import SemanticGraph


class ChangeKind(Enum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


@dataclass(frozen=True)
class Change:
    """One object that differs between two snapshots."""

    node_id: str
    kind: ChangeKind
    object_kind: str
    before: ObjectRecord | None = None
    after: ObjectRecord | None = None

    @property
    def summary(self) -> str:
        name = self.node_id.split(":", 1)[-1]
        return f"{self.kind.value} {self.object_kind} {name}"


@dataclass(frozen=True)
class AffectedRequirement:
    """A requirement whose bound implementation moved beneath it."""

    requirement: Requirement
    #: The changes its evidence points at.
    changes: tuple[Change, ...]

    @property
    def id(self) -> str:
        return self.requirement.id


@dataclass
class DriftReport:
    before_label: str
    after_label: str
    model_name: str
    changes: list[Change] = field(default_factory=list)
    affected: list[AffectedRequirement] = field(default_factory=list)
    #: Objects present and identical in both. Counted, never listed.
    unchanged: int = 0

    @property
    def has_drift(self) -> bool:
        return bool(self.changes)

    def of_kind(self, kind: ChangeKind) -> list[Change]:
        return [c for c in self.changes if c.kind is kind]

    def counts(self) -> dict[str, int]:
        return {
            "added": len(self.of_kind(ChangeKind.ADDED)),
            "removed": len(self.of_kind(ChangeKind.REMOVED)),
            "changed": len(self.of_kind(ChangeKind.CHANGED)),
            "unchanged": self.unchanged,
            "affected_requirements": len(self.affected),
        }


def compare(
    before: Snapshot,
    after: Snapshot,
    after_graph: SemanticGraph | None = None,
) -> DriftReport:
    """Diff two snapshots and, given the current model, name what is now suspect.

    ``after_graph`` is optional because object-level drift is answerable from
    the snapshots alone. Requirement impact needs the live model, since
    requirements are re-derived rather than stored -- see the snapshot module for
    why.
    """
    report = DriftReport(
        before_label=before.label or before.taken_at,
        after_label=after.label or after.taken_at,
        model_name=after.model_name,
    )

    for node_id, old in before.objects.items():
        new = after.objects.get(node_id)
        if new is None:
            report.changes.append(
                Change(node_id, ChangeKind.REMOVED, old.kind, before=old)
            )
        elif old.fingerprint != new.fingerprint:
            report.changes.append(
                Change(node_id, ChangeKind.CHANGED, new.kind, before=old, after=new)
            )
        else:
            report.unchanged += 1

    for node_id, new in after.objects.items():
        if node_id not in before.objects:
            report.changes.append(
                Change(node_id, ChangeKind.ADDED, new.kind, after=new)
            )

    report.changes.sort(key=lambda c: (c.kind.value, c.node_id))

    if after_graph is not None:
        report.affected = _affected_requirements(report.changes, before, after_graph)

    return report


def _affected_requirements(
    changes: list[Change],
    before: Snapshot,
    after_graph: SemanticGraph,
) -> list[AffectedRequirement]:
    """Requirements whose evidence points at something that moved.

    Matching is by node id rather than by fingerprint: a requirement derived
    from the new model necessarily carries the new fingerprint, so comparing
    fingerprints would find nothing. What matters is whether the object a
    requirement rests on is one that changed.

    Requirements are derived from the *new* model, so a requirement retired by a
    deletion cannot appear here -- there is no longer an object to derive it
    from. Deletions are reported in the change list instead, which is where a
    reader looks for them; naming the retired requirement as well would mean
    carrying the old model through the comparison for something already visible.
    """
    by_node: dict[str, list[Change]] = {}
    for change in changes:
        by_node.setdefault(change.node_id, []).append(change)

    affected: list[AffectedRequirement] = []
    for requirement in RequirementDeriver(after_graph).derive():
        touched: list[Change] = []
        for evidence in requirement.evidence:
            touched.extend(by_node.get(evidence.node_id, []))
        if touched:
            affected.append(
                AffectedRequirement(requirement=requirement, changes=tuple(touched))
            )

    affected.sort(key=lambda a: a.id)
    return affected


def to_text(report: DriftReport) -> str:
    """Render a report for a terminal."""
    counts = report.counts()
    lines: list[str] = []

    lines.append(f"Drift: {report.model_name}")
    lines.append(f"  {report.before_label}  ->  {report.after_label}")
    lines.append("=" * 68)

    if not report.has_drift:
        lines.append(f"  No drift. {counts['unchanged']} objects unchanged.")
        return "\n".join(lines)

    lines.append(
        f"  {counts['changed']} changed, {counts['added']} added, "
        f"{counts['removed']} removed, {counts['unchanged']} unchanged"
    )

    for kind, heading in (
        (ChangeKind.CHANGED, "Changed"),
        (ChangeKind.ADDED, "Added"),
        (ChangeKind.REMOVED, "Removed"),
    ):
        items = report.of_kind(kind)
        if not items:
            continue
        lines.append("")
        lines.append(f"{heading} ({len(items)})")
        lines.append("-" * 68)
        for change in items:
            name = change.node_id.split(":", 1)[-1]
            lines.append(f"  {change.object_kind:14} {name}")
            if change.kind is ChangeKind.CHANGED:
                lines.append(f"      before: {_clip(change.before.detail)}")
                lines.append(f"      after:  {_clip(change.after.detail)}")
                lines.append(
                    f"      {change.before.fingerprint[:12]} -> "
                    f"{change.after.fingerprint[:12]}"
                )
            elif change.after is not None and change.after.detail:
                lines.append(f"      {_clip(change.after.detail)}")
            elif change.before is not None and change.before.detail:
                lines.append(f"      was: {_clip(change.before.detail)}")

    if report.affected:
        lines.append("")
        lines.append(f"Requirements now in question ({len(report.affected)})")
        lines.append("-" * 68)
        for item in report.affected:
            lines.append(f"  {item.id}  {_clip(_plain(item.requirement.statement), 88)}")
            for change in item.changes:
                lines.append(f"      via {change.summary}")

    return "\n".join(lines)


def _clip(text: str, width: int = 78) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _plain(text: str) -> str:
    return text.replace("**", "").replace("`", "")

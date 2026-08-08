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
    #: Same logic, new name. See ``_pair_renames`` for why this is provable here
    #: rather than guessed.
    RENAMED = "renamed"


def _name_of(node_id: str) -> str:
    return node_id.split(":", 1)[-1]


@dataclass(frozen=True)
class Change:
    """One object that differs between two snapshots.

    For a rename, ``node_id`` is the object's *current* identity and
    ``before.node_id`` holds the one it replaced -- the new name is what a
    reader will search for in the model they now have.
    """

    node_id: str
    kind: ChangeKind
    object_kind: str
    before: ObjectRecord | None = None
    after: ObjectRecord | None = None

    @property
    def summary(self) -> str:
        if self.kind is ChangeKind.RENAMED and self.before is not None:
            return (
                f"renamed {self.object_kind} "
                f"{_name_of(self.before.node_id)} -> {_name_of(self.node_id)}"
            )
        return f"{self.kind.value} {self.object_kind} {_name_of(self.node_id)}"

    @property
    def is_semantic(self) -> bool:
        """Did what this object *computes* change?

        A rename did not. The distinction is what lets a reviewer skip
        re-validating logic that is provably identical, and it is the reason
        renames are separated out rather than folded into added/removed.
        """
        return self.kind is not ChangeKind.RENAMED


@dataclass(frozen=True)
class AffectedRequirement:
    """A requirement whose bound implementation moved beneath it."""

    requirement: Requirement
    #: The changes its evidence points at.
    changes: tuple[Change, ...]

    @property
    def id(self) -> str:
        return self.requirement.id

    @property
    def needs_revalidation(self) -> bool:
        """Must a person re-confirm this requirement still holds?

        Only when something it rests on actually changed what it computes. A
        requirement touched solely by renames needs its wording updated to the
        new name and nothing more -- treating that as re-validation is exactly
        the busywork this report exists to remove.
        """
        return any(change.is_semantic for change in self.changes)


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
            "renamed": len(self.of_kind(ChangeKind.RENAMED)),
            "unchanged": self.unchanged,
            "affected_requirements": len(self.affected),
            "needing_revalidation": len(self.needing_revalidation),
            "reference_updates_only": len(self.reference_updates_only),
        }

    @property
    def needing_revalidation(self) -> list[AffectedRequirement]:
        """Requirements a person must re-confirm."""
        return [a for a in self.affected if a.needs_revalidation]

    @property
    def reference_updates_only(self) -> list[AffectedRequirement]:
        """Requirements whose only change is a name they refer to."""
        return [a for a in self.affected if not a.needs_revalidation]

    @property
    def semantic_changes(self) -> list[Change]:
        """Changes that altered what the model computes.

        The number a reviewer actually has to act on: renames are excluded
        because their logic is provably unchanged.
        """
        return [c for c in self.changes if c.is_semantic]


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

    report.changes = _pair_renames(report.changes)
    report.changes.sort(key=lambda c: (c.kind.value, c.node_id))

    if after_graph is not None:
        report.affected = _affected_requirements(report.changes, before, after_graph)

    return report


def _pair_renames(changes: list[Change]) -> list[Change]:
    """Recognise a removal and an addition as one object under a new name.

    This is provable here rather than guessed, which is the whole point. Every
    other diff tool infers renames from how similar two names look -- a
    heuristic that mistakes ``Net Sales`` for ``Net Sales PM`` and misses
    ``OOS Rate`` becoming ``Out Of Spec Rate`` entirely. Because a fingerprint
    is taken over canonicalised logic rather than text, two objects sharing one
    means they compute exactly the same thing, and the name is the only thing
    that moved.

    Ambiguity is refused rather than resolved. Two measures can legitimately
    share a fingerprint -- ``COUNTROWS(Batch)`` written twice under different
    names is one fingerprint, two objects -- so a pairing is only made when a
    fingerprint identifies exactly one removal and exactly one addition of the
    same kind. Anything else stays reported as it was: a wrong pairing would
    tell a reviewer that logic is unchanged when nobody has established that,
    which is the one error this project exists to avoid making.
    """
    removed = [c for c in changes if c.kind is ChangeKind.REMOVED]
    added = [c for c in changes if c.kind is ChangeKind.ADDED]
    if not removed or not added:
        return changes

    def key(change: Change) -> tuple[str, str]:
        record = change.before or change.after
        assert record is not None  # every removal/addition carries one side
        return (change.object_kind, record.fingerprint)

    removed_by_key: dict[tuple[str, str], list[Change]] = {}
    added_by_key: dict[tuple[str, str], list[Change]] = {}
    for change in removed:
        removed_by_key.setdefault(key(change), []).append(change)
    for change in added:
        added_by_key.setdefault(key(change), []).append(change)

    renames: dict[str, Change] = {}
    paired: set[str] = set()

    for shared in removed_by_key.keys() & added_by_key.keys():
        gone = removed_by_key[shared]
        arrived = added_by_key[shared]
        if len(gone) != 1 or len(arrived) != 1:
            continue  # ambiguous -- say nothing rather than guess

        old, new = gone[0], arrived[0]
        renames[new.node_id] = Change(
            node_id=new.node_id,
            kind=ChangeKind.RENAMED,
            object_kind=new.object_kind,
            before=old.before,
            after=new.after,
        )
        paired.update({old.node_id, new.node_id})

    return [
        renames.get(c.node_id, c)
        for c in changes
        if not (c.kind is ChangeKind.REMOVED and c.node_id in paired)
    ]


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

    summary = (
        f"  {counts['changed']} changed, {counts['added']} added, "
        f"{counts['removed']} removed, {counts['unchanged']} unchanged"
    )
    if counts["renamed"]:
        # Stated separately from the rest because it is the one line here that
        # needs no action: the logic behind a rename is provably untouched.
        summary += f"\n  {counts['renamed']} renamed, logic unchanged"
    lines.append(summary)

    for kind, heading in (
        (ChangeKind.CHANGED, "Changed"),
        (ChangeKind.ADDED, "Added"),
        (ChangeKind.REMOVED, "Removed"),
        (ChangeKind.RENAMED, "Renamed — same logic, new name"),
    ):
        items = report.of_kind(kind)
        if not items:
            continue
        lines.append("")
        lines.append(f"{heading} ({len(items)})")
        lines.append("-" * 68)
        for change in items:
            name = _name_of(change.node_id)
            if change.kind is ChangeKind.RENAMED:
                lines.append(
                    f"  {change.object_kind:14} "
                    f"{_name_of(change.before.node_id)}  ->  {name}"
                )
                lines.append(
                    f"      fingerprint {change.after.fingerprint[:12]} on both sides"
                )
                continue

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

    for items, heading in (
        (report.needing_revalidation, "Requirements now in question"),
        (
            report.reference_updates_only,
            "Requirements needing only a name update — logic unchanged",
        ),
    ):
        if not items:
            continue
        lines.append("")
        lines.append(f"{heading} ({len(items)})")
        lines.append("-" * 68)
        for item in items:
            lines.append(f"  {item.id}  {_clip(_plain(item.requirement.statement), 88)}")
            for change in item.changes:
                lines.append(f"      via {change.summary}")

    return "\n".join(lines)


def _clip(text: str, width: int = 78) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _plain(text: str) -> str:
    return text.replace("**", "").replace("`", "")

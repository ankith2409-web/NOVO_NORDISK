"""Snapshot comparison and drift detection.

Run against two genuinely authored versions of the clinical model rather than a
synthetic mutation, because the property that matters is not "a hash changed" --
it is that the *right* things register and the wrong things stay silent.

v2 contains six deliberate edits covering every kind of change the report has to
distinguish:

  1. Serious Adverse Events gains a causality filter  -> semantic, must drift
  2. Protocol Deviations is reformatted and commented -> cosmetic, must NOT drift
  3. An enrolment threshold moves 0.8 -> 0.9          -> semantic, must drift
  4. Expedited Reporting Compliance is added
  5. Deviations per Site is removed
  6. An inactive relationship is activated, and a hierarchy gains a level

Case 2 is the one that earns trust. A report that fires on whitespace is a
report nobody reads, so its silence is asserted as firmly as the others' noise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.drift import snapshot as snap
from concordance.drift.compare import ChangeKind, compare, to_text
from concordance.generate.requirements import RequirementDeriver
from concordance.graph.csg import SemanticGraph, measure_id

V1 = Path("data/models/ClinicalTrialSafety.SemanticModel")
V2 = Path("data/models/ClinicalTrialSafety_v2.SemanticModel")


def _graph(path: Path) -> SemanticGraph:
    if not path.exists():
        pytest.skip(f"model not present: {path}")
    return SemanticGraph(TmdlAdapter().extract(str(path)))


@pytest.fixture(scope="module")
def v1() -> SemanticGraph:
    return _graph(V1)


@pytest.fixture(scope="module")
def v2() -> SemanticGraph:
    return _graph(V2)


@pytest.fixture(scope="module")
def report(v1: SemanticGraph, v2: SemanticGraph):
    return compare(snap.take(v1, "v1"), snap.take(v2, "v2"), after_graph=v2)


def _changed_ids(report) -> set[str]:
    return {c.node_id for c in report.of_kind(ChangeKind.CHANGED)}


# -- the silence that makes the report trustworthy ---------------------------

def test_a_reformatted_measure_does_not_register_as_drift(report) -> None:
    """Comments, spacing and casing changed; meaning did not."""
    assert (
        measure_id("Clinical Metrics", "Protocol Deviations") not in _changed_ids(report)
    )


def test_the_reformatted_measure_really_did_change_on_disk(
    v1: SemanticGraph, v2: SemanticGraph
) -> None:
    """Guards the test above from passing for the wrong reason.

    If v2 were accidentally identical to v1, the silence test would pass while
    proving nothing. The text must differ and the fingerprint must not.
    """
    before = next(m for m in v1.model.measures if m.name == "Protocol Deviations")
    after = next(m for m in v2.model.measures if m.name == "Protocol Deviations")

    assert before.expression.strip() != after.expression.strip()
    assert "reformatted" in after.expression
    assert before.fingerprint == after.fingerprint


def test_unchanged_objects_are_counted_not_listed(report) -> None:
    assert report.counts()["unchanged"] > 90
    assert len(report.changes) < 10


# -- what must register ------------------------------------------------------

def test_an_added_filter_registers_as_a_changed_measure(report) -> None:
    node = measure_id("Clinical Metrics", "Serious Adverse Events")
    assert node in _changed_ids(report)

    change = next(c for c in report.changes if c.node_id == node)
    assert "Causality" in change.after.detail
    assert "Causality" not in change.before.detail
    assert change.before.fingerprint != change.after.fingerprint


def test_a_moved_threshold_registers(report) -> None:
    """0.8 -> 0.9 changes which studies read as "approaching target"."""
    node = measure_id("Clinical Metrics", "Enrollment Status")
    assert node in _changed_ids(report)


def test_a_new_measure_is_reported_as_added(report) -> None:
    added = {c.node_id for c in report.of_kind(ChangeKind.ADDED)}
    assert measure_id("Clinical Metrics", "Expedited Reporting Compliance") in added


def test_a_deleted_measure_is_reported_as_removed(report) -> None:
    removed = {c.node_id for c in report.of_kind(ChangeKind.REMOVED)}
    assert measure_id("Clinical Metrics", "Deviations per Site") in removed


def test_activating_a_relationship_registers(report) -> None:
    """Cardinality and direction govern filter flow, so this is not cosmetic."""
    change = next(
        c for c in report.changes
        if c.object_kind == "relationship" and "AdverseEvent" in c.node_id
    )
    assert "inactive" in change.before.detail
    assert "active" in change.after.detail


def test_an_added_hierarchy_level_registers(report) -> None:
    change = next(c for c in report.changes if c.object_kind == "hierarchy")
    assert change.before.detail == "System Organ Class -> Preferred Term"
    assert change.after.detail.endswith("Severity")


# -- the binding: which requirements are now in question ---------------------

def test_requirement_ids_survive_the_change_they_describe(
    v1: SemanticGraph, v2: SemanticGraph
) -> None:
    """The property drift reporting depends on.

    If ids were derived from an expression, editing a measure would retire one
    requirement and mint another, and nothing would be reportable as drifted.
    """
    node = measure_id("Clinical Metrics", "Serious Adverse Events")

    def bound_to(graph: SemanticGraph) -> dict[str, str]:
        return {
            r.id: r.evidence[0].fingerprint
            for r in RequirementDeriver(graph).derive()
            if any(e.node_id == node for e in r.evidence)
        }

    before, after = bound_to(v1), bound_to(v2)
    assert set(before) == set(after), "requirement ids must not move"
    assert before != after, "the bound fingerprints must move"


def test_affected_requirements_name_the_change_that_touched_them(report) -> None:
    affected = {a.id: a for a in report.affected}
    assert "REQ-F-ab8582" in affected  # Serious Adverse Events

    item = affected["REQ-F-ab8582"]
    assert any(c.kind is ChangeKind.CHANGED for c in item.changes)
    assert any("Serious Adverse Events" in c.node_id for c in item.changes)


def test_untouched_requirements_are_not_flagged(report) -> None:
    """Only what actually moved should be in question."""
    flagged = {a.id for a in report.affected}
    untouched = [
        r
        for r in RequirementDeriver(_graph(V2)).derive()
        if r.category == "Metrics and KPIs" and r.id not in flagged
    ]
    assert untouched, "most metrics were untouched and must not be flagged"


# -- snapshots ---------------------------------------------------------------

def test_a_snapshot_round_trips_through_disk(v1: SemanticGraph, tmp_path: Path) -> None:
    original = snap.take(v1, "v1")
    restored = snap.Snapshot.load(original.save(tmp_path / "v1.json"))

    assert restored.model_name == original.model_name
    assert restored.label == "v1"
    assert restored.fingerprints == original.fingerprints


def test_comparing_a_snapshot_with_itself_finds_nothing(v1: SemanticGraph) -> None:
    taken = snap.take(v1, "v1")
    report = compare(taken, taken)

    assert not report.has_drift
    assert report.counts()["unchanged"] == len(taken.objects)


def test_snapshots_capture_relationships_which_live_on_edges(v1: SemanticGraph) -> None:
    taken = snap.take(v1, "v1")
    relationships = [r for r in taken.objects.values() if r.kind == "relationship"]
    assert len(relationships) == 10


def test_a_snapshot_from_a_future_schema_is_refused(tmp_path: Path) -> None:
    """Silently misreading a newer file would produce a wrong drift report."""
    import json

    path = tmp_path / "future.json"
    path.write_text(json.dumps({"schema_version": 99, "model_name": "x", "objects": []}))

    with pytest.raises(ValueError, match="schema version"):
        snap.Snapshot.load(path)


def test_a_missing_snapshot_raises_clearly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        snap.Snapshot.load(tmp_path / "nope.json")


# -- rendering ---------------------------------------------------------------

def test_the_text_report_shows_both_sides_of_a_change(report) -> None:
    text = to_text(report)
    assert "Serious Adverse Events" in text
    assert "before:" in text and "after:" in text
    assert "Requirements now in question" in text

    # The reformatted measure must not be listed as a changed object. Its name
    # does still appear in the report -- the deleted "Deviations per Site"
    # references it as DIVIDE([Protocol Deviations], ...) -- so a bare substring
    # check would pass or fail for reasons unrelated to what is being asserted.
    listed = [
        line.strip()
        for line in text.splitlines()
        if line.startswith("  measure ") or line.startswith("  hierarchy ")
    ]
    assert not any("Clinical Metrics[Protocol Deviations]" in line for line in listed)


def test_a_clean_report_says_so_plainly(v1: SemanticGraph) -> None:
    text = to_text(compare(snap.take(v1, "a"), snap.take(v1, "b")))
    assert "No drift" in text

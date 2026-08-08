"""Telling a rename apart from a real change.

Renaming a measure used to be reported as a deletion plus an addition, which is
what every fingerprint-free diff does and what an adversarial review of this
project correctly flagged. The consequence was not cosmetic: on the
QualityControl model a single cosmetic rename churned four requirements, each of
which reads to a reviewer as something to re-validate.

What makes this provable rather than guessed is the fingerprint. Other tools
infer renames from name similarity, which mistakes ``Net Sales`` for
``Net Sales PM`` and misses ``OOS Rate`` becoming ``Out Of Spec Rate``
altogether. A fingerprint is taken over canonicalised logic, so two objects
sharing one compute exactly the same thing and only the name moved.

The tests that matter most here are the ones asserting it *refuses* to pair
when the evidence is ambiguous. Claiming "logic unchanged" about two objects
nobody has established are the same is the single worst error this report could
make.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.drift import snapshot as snap
from concordance.drift.compare import ChangeKind, compare, to_text
from concordance.graph.csg import SemanticGraph

MODEL = Path("data/models/QualityControl.SemanticModel")
MEASURES = "definition/tables/QC Metrics.tmdl"


def _load(path: Path) -> SemanticGraph:
    return SemanticGraph(TmdlAdapter().extract(str(path)))


@pytest.fixture(scope="module")
def original() -> SemanticGraph:
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return _load(MODEL)


def _variant(tmp_path: Path, *edits: tuple[str, str]) -> SemanticGraph:
    """A copy of the model with literal substitutions applied to its measures."""
    target = tmp_path / "variant"
    shutil.copytree(MODEL, target)
    file = target / MEASURES
    text = file.read_text(encoding="utf-8")
    for old, new in edits:
        assert old in text, f"fixture no longer contains {old!r}"
        text = text.replace(old, new)
    file.write_text(text, encoding="utf-8")
    return _load(target)


def _drift(before: SemanticGraph, after: SemanticGraph):
    return compare(snap.take(before, "v1"), snap.take(after, "v2"), after_graph=after)


# -- the rename itself --------------------------------------------------------

def test_a_pure_rename_is_one_change_not_two(original, tmp_path) -> None:
    after = _variant(tmp_path, ("measure 'OOS Rate' =", "measure 'Out Of Spec Rate' ="))
    report = _drift(original, after)

    assert report.counts()["renamed"] == 1
    assert report.counts()["added"] == 0
    assert report.counts()["removed"] == 0

    rename = report.of_kind(ChangeKind.RENAMED)[0]
    assert "OOS Rate" in rename.before.node_id
    assert "Out Of Spec Rate" in rename.after.node_id
    # The identical fingerprint is the evidence, not a similar-looking name.
    assert rename.before.fingerprint == rename.after.fingerprint


def test_a_rename_needs_no_revalidation(original, tmp_path) -> None:
    """The whole point: unchanged logic must not generate re-validation work."""
    after = _variant(tmp_path, ("measure 'OOS Rate' =", "measure 'Out Of Spec Rate' ="))
    report = _drift(original, after)

    assert report.semantic_changes == []
    assert report.needing_revalidation == []
    # The requirements still moved -- they name the measure -- but as a wording
    # update rather than a question about whether they still hold.
    assert report.reference_updates_only


def test_the_rename_names_both_sides_in_its_summary(original, tmp_path) -> None:
    after = _variant(tmp_path, ("measure 'OOS Rate' =", "measure 'Out Of Spec Rate' ="))
    rename = _drift(original, after).of_kind(ChangeKind.RENAMED)[0]
    before, _, after_name = rename.summary.partition(" -> ")
    assert before.endswith("[OOS Rate]"), rename.summary
    assert after_name.endswith("[Out Of Spec Rate]"), rename.summary


# -- what must NOT be called a rename -----------------------------------------

def test_a_rename_with_an_edit_is_not_a_rename(original, tmp_path) -> None:
    """Changing the logic while renaming moves the fingerprint, so no pairing.

    This is the case a name-similarity heuristic gets dangerously wrong: the
    names are close, so it reports a rename and a reviewer concludes nothing
    needs checking -- when in fact the denominator changed.
    """
    after = _variant(
        tmp_path,
        (
            "measure 'OOS Rate' = DIVIDE([OOS Results], [Tests Performed], 0)",
            "measure 'Out Of Spec Rate' = DIVIDE([OOS Results], [Batches Manufactured], 0)",
        ),
    )
    report = _drift(original, after)

    assert report.counts()["renamed"] == 0
    assert report.counts()["added"] == 1
    assert report.counts()["removed"] == 1


def test_an_ambiguous_fingerprint_is_never_paired(original, tmp_path) -> None:
    """Two objects sharing a fingerprint cannot be told apart, so neither pairs.

    ``Batches Manufactured`` and ``Tests Performed`` are both a bare COUNTROWS.
    Renaming both at once leaves two removals and two additions under one
    fingerprint each -- and where a fingerprint is genuinely shared, guessing a
    pairing would assert "logic unchanged" about a mapping nobody established.
    """
    # Give two measures byte-identical logic, then rename both. Either old name
    # could map to either new one; the evidence does not say which.
    after = _variant(
        tmp_path,
        ("measure 'Batches Manufactured' = COUNTROWS(Batch)", "measure 'Alpha' = COUNTROWS(Site)"),
        ("measure 'Tests Performed' = COUNTROWS(TestResult)", "measure 'Beta' = COUNTROWS(Site)"),
    )
    report = _drift(original, after)

    arrived = [
        c for c in report.of_kind(ChangeKind.ADDED)
        if c.node_id.endswith(("[Alpha]", "[Beta]"))
    ]
    assert len(arrived) == 2, "the fixture must produce two colliding additions"
    assert len({c.after.fingerprint for c in arrived}) == 1, "their logic must collide"

    # Reported plainly as removals and additions. Naming either pairing would
    # assert unchanged logic about a mapping nothing established.
    assert report.of_kind(ChangeKind.RENAMED) == []


def test_a_change_without_a_rename_still_reports_normally(original, tmp_path) -> None:
    """The pairing pass must not disturb an ordinary edit."""
    after = _variant(
        tmp_path,
        ("DIVIDE([OOS Results], [Tests Performed], 0)", "DIVIDE([OOS Results], [Tests Performed], BLANK())"),
    )
    report = _drift(original, after)

    assert report.counts()["changed"] == 1
    assert report.counts()["renamed"] == 0
    assert report.needing_revalidation, "a real edit must still raise a question"


def test_an_addition_alone_is_not_paired_with_anything(original, tmp_path) -> None:
    after = _variant(
        tmp_path,
        (
            "measure 'Tests Performed' = COUNTROWS(TestResult)",
            "measure 'Tests Performed' = COUNTROWS(TestResult)\n\n"
            "\tmeasure 'Brand New' = COUNTROWS(Site)",
        ),
    )
    report = _drift(original, after)

    assert report.counts()["added"] == 1
    assert report.counts()["renamed"] == 0


# -- rendering ----------------------------------------------------------------

def test_the_report_separates_revalidation_from_a_name_update(original, tmp_path) -> None:
    after = _variant(tmp_path, ("measure 'OOS Rate' =", "measure 'Out Of Spec Rate' ="))
    text = to_text(_drift(original, after))

    assert "renamed, logic unchanged" in text
    assert "only a name update" in text
    assert "Requirements now in question" not in text


# -- the CI gate --------------------------------------------------------------
# The exit code is the whole feature: it is what a pipeline reads. A gate that
# fires on a rename is one people route around, so these pin both directions.

@pytest.mark.parametrize(
    "mode,expected",
    [("none", 0), ("revalidation", 0), ("semantic", 0), ("any", 1)],
)
def test_a_rename_only_fails_the_noisiest_gate(tmp_path, mode: str, expected: int) -> None:
    from concordance.cli import main

    after = _variant(tmp_path, ("measure 'OOS Rate' =", "measure 'Out Of Spec Rate' ="))
    code = main([
        "drift", str(MODEL), str(tmp_path / "variant"),
        "--allow-different-models", "--fail-on", mode,
    ])
    assert code == expected
    assert after is not None  # the variant was built


@pytest.mark.parametrize("mode,expected", [("none", 0), ("revalidation", 1), ("any", 1)])
def test_a_real_change_fails_the_revalidation_gate(tmp_path, mode: str, expected: int) -> None:
    from concordance.cli import main

    _variant(
        tmp_path,
        ("DIVIDE([OOS Results], [Tests Performed], 0)",
         "DIVIDE([OOS Results], [Batches Manufactured], 0)"),
    )
    code = main([
        "drift", str(MODEL), str(tmp_path / "variant"),
        "--allow-different-models", "--fail-on", mode,
    ])
    assert code == expected


def test_the_gate_is_off_unless_asked_for(tmp_path) -> None:
    """Someone running drift to look at it must not get a non-zero exit."""
    from concordance.cli import main

    _variant(
        tmp_path,
        ("DIVIDE([OOS Results], [Tests Performed], 0)",
         "DIVIDE([OOS Results], [Batches Manufactured], 0)"),
    )
    assert main(["drift", str(MODEL), str(tmp_path / "variant"),
                 "--allow-different-models"]) == 0

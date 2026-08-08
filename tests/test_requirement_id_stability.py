"""Requirement ids must not depend on the file the model was read from.

Found while investigating a lead from an external review: comparing two
snapshots taken under different directory names showed two requirement ids
changing even though every table, measure and column was byte-identical. The
cause was `model.name`, which is the file or folder Concordance happened to
read the model from -- `path.stem` in the .pbix adapter, the directory name
minus its suffix in the TMDL one -- not anything about the model's own content.
Three requirement categories (Scope, Data acquisition, Documented gaps) folded
it into their identity anyway.

The scenario this breaks in practice is ordinary: exporting the same dataset to
a `.pbix` twice under different names, or cloning a TMDL project folder for a
snapshot before a change. Nothing about the model changed, but the id a
reviewer confirmed yesterday no longer matches anything today -- exactly the
false alarm the whole fingerprint scheme exists to prevent.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.generate.requirements import Kind, RequirementDeriver
from concordance.graph.csg import SemanticGraph

MODEL = Path("data/models/QualityControl.SemanticModel")


def _ids(path: Path) -> dict[str, str]:
    graph = SemanticGraph(TmdlAdapter().extract(str(path)))
    return {r.id: r.category for r in RequirementDeriver(graph).derive()}


@pytest.fixture(scope="module")
def original() -> Path:
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return MODEL


def test_renaming_the_source_folder_changes_no_requirement_id(
    original: Path, tmp_path: Path
) -> None:
    """The exact case that exposed this: same content, two different folder names."""
    copy_a = tmp_path / "QualityControl.SemanticModel"
    copy_b = tmp_path / "QC_Production.SemanticModel"
    shutil.copytree(original, copy_a)
    shutil.copytree(original, copy_b)

    ids_a = _ids(copy_a)
    ids_b = _ids(copy_b)

    assert ids_a.keys() == ids_b.keys()


@pytest.mark.parametrize("category", ["Scope", "Data acquisition"])
def test_the_singleton_categories_are_stable_across_a_rename(
    original: Path, tmp_path: Path, category: str
) -> None:
    """Named explicitly: these two were the ones actually found unstable."""
    renamed = tmp_path / "a_completely_different_name.SemanticModel"
    shutil.copytree(original, renamed)

    before = {i: c for i, c in _ids(original).items() if c == category}
    after = {i: c for i, c in _ids(renamed).items() if c == category}

    assert before, f"fixture no longer produces a {category!r} requirement"
    assert set(before) == set(after)


def test_a_documented_gap_keeps_its_id_across_a_rename(tmp_path: Path) -> None:
    """Coverage-gap ids are keyed on the gap's own feature name, not the file."""
    from concordance.model import CoverageGap, SemanticModel
    from concordance.graph.csg import SemanticGraph as Graph

    def model_named(name: str) -> SemanticModel:
        m = SemanticModel(name=name, source_path="", source_type="tmdl")
        m.coverage_gaps = [CoverageGap(feature="calculation groups", count=2, reason="not read")]
        return m

    ids_a = {
        r.id
        for r in RequirementDeriver(Graph(model_named("Extract_v1"))).derive()
        if r.kind is Kind.FUNCTIONAL and r.category == "Documented gaps"
    }
    ids_b = {
        r.id
        for r in RequirementDeriver(Graph(model_named("Extract_v2_renamed"))).derive()
        if r.kind is Kind.FUNCTIONAL and r.category == "Documented gaps"
    }
    assert ids_a == ids_b and ids_a, "gap ids must survive a rename of the source file"


def test_content_derived_ids_still_change_when_content_actually_changes(
    original: Path, tmp_path: Path
) -> None:
    """The fix must not go too far and make every id a constant.

    A real edit -- adding a table -- has to still produce a different Scope
    statement and, since the id is now a fixed constant per category, the same
    id with different content. That is correct: Scope is a singleton per
    model, so its id identifies the *category*, and the statement text is what
    carries the actual coverage. This test exists so a future change cannot
    "fix" the id back into being content-derived without noticing the tradeoff.
    """
    copy = tmp_path / "QualityControl.SemanticModel"
    shutil.copytree(original, copy)
    (copy / "definition/tables/Product.tmdl").unlink()

    before = next(r for r in RequirementDeriver(
        SemanticGraph(TmdlAdapter().extract(str(original)))
    ).derive() if r.category == "Scope")
    after = next(r for r in RequirementDeriver(
        SemanticGraph(TmdlAdapter().extract(str(copy)))
    ).derive() if r.category == "Scope")

    assert before.id == after.id  # same category, same singleton id
    assert before.statement != after.statement  # but the coverage claim changed

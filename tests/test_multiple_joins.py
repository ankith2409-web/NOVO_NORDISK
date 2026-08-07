"""Two tables joined more than once.

A role-playing date dimension -- one fact table joined to Calendar on several
date columns -- is one of the commonest shapes in Power BI. The graph was a
``DiGraph``, which permits a single edge per node pair, so every join after the
first was silently overwritten.

It survived every earlier test because the models in the suite happened not to
contain the pattern: the clinical model's inactive joins sit on different table
pairs. AdventureWorks does contain it, and was quietly losing two of its three
Sales-to-Date joins -- keeping the *inactive* ShipDate one and discarding the
active OrderDate join that is the model's default filter path.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.adapters.tmdl import TmdlAdapter
from concordance.drift import snapshot as snap
from concordance.graph.csg import SemanticGraph

ADVENTURE_WORKS = Path("data/models/AdventureWorks_Sales.pbix")
QUALITY_CONTROL = Path("data/models/QualityControl.SemanticModel")


def _pbix(path: Path):
    if not path.exists():
        pytest.skip(f"model not present: {path}")
    return PbixAdapter().extract(str(path))


def _tmdl(path: Path):
    if not path.exists():
        pytest.skip(f"model not present: {path}")
    return TmdlAdapter().extract(str(path))


# -- the loss itself ---------------------------------------------------------

@pytest.mark.parametrize(
    "loader,path",
    [(_pbix, ADVENTURE_WORKS), (_tmdl, QUALITY_CONTROL)],
)
def test_every_extracted_join_reaches_the_graph(loader, path: Path) -> None:
    """The count is the whole assertion: nothing may be dropped in between."""
    model = loader(path)
    graph = SemanticGraph(model)
    assert len(graph.join_paths()) == len(model.relationships)


def test_adventureworks_keeps_all_three_date_joins() -> None:
    """Sales joins Date on OrderDate, DueDate and ShipDate."""
    graph = SemanticGraph(_pbix(ADVENTURE_WORKS))
    sales_to_date = [
        j for j in graph.join_paths() if j["from"] == "Sales" and j["to"] == "Date"
    ]

    assert len(sales_to_date) == 3
    assert {j["from_column"] for j in sales_to_date} == {
        "OrderDateKey",
        "DueDateKey",
        "ShipDateKey",
    }


def test_the_active_join_is_not_the_one_that_gets_dropped() -> None:
    """The failure kept an inactive join and discarded the model's default path."""
    graph = SemanticGraph(_pbix(ADVENTURE_WORKS))
    active = [
        j
        for j in graph.join_paths()
        if j["from"] == "Sales" and j["to"] == "Date" and j["is_active"]
    ]
    assert len(active) == 1
    assert active[0]["from_column"] == "OrderDateKey"


def test_a_measure_can_reach_the_relationship_it_activates() -> None:
    """`Sales Amount by Due Date` calls USERELATIONSHIP on a join that was missing."""
    model = _pbix(ADVENTURE_WORKS)
    graph = SemanticGraph(model)

    measure = next(m for m in model.measures if m.name == "Sales Amount by Due Date")
    assert "USERELATIONSHIP" in measure.expression.upper()

    assert any(
        j["from_column"] == "DueDateKey" and not j["is_active"]
        for j in graph.join_paths()
    )


def test_quality_control_keeps_both_date_roles() -> None:
    graph = SemanticGraph(_tmdl(QUALITY_CONTROL))
    batch_to_calendar = [
        j for j in graph.join_paths() if j["from"] == "Batch" and j["to"] == "Calendar"
    ]

    assert len(batch_to_calendar) == 2
    by_column = {j["from_column"]: j for j in batch_to_calendar}
    assert by_column["ManufactureDate"]["is_active"]
    assert not by_column["ReleaseDate"]["is_active"]


# -- the fix must not introduce duplicates ------------------------------------

@pytest.mark.parametrize(
    "loader,path",
    [(_pbix, ADVENTURE_WORKS), (_tmdl, QUALITY_CONTROL)],
)
def test_no_join_is_recorded_twice(loader, path: Path) -> None:
    """A multigraph makes duplication possible; keying the edge prevents it."""
    graph = SemanticGraph(loader(path))
    seen = Counter(
        (j["from"], j["from_column"], j["to"], j["to_column"])
        for j in graph.join_paths()
    )
    assert not [key for key, count in seen.items() if count > 1]


def test_dependency_edges_are_not_duplicated_by_the_multigraph() -> None:
    """Non-join edges are keyed by kind, so re-adding one updates it."""
    graph = SemanticGraph(_tmdl(QUALITY_CONTROL))
    from concordance.graph.csg import measure_id

    node = measure_id("QC Metrics", "OOS Rate")
    dependencies = graph.dependencies_of(node)
    assert len(dependencies) == len(set(dependencies))


# -- everything downstream must see the recovered joins -----------------------

def test_snapshots_capture_every_join() -> None:
    """Drift over a lost relationship could never have been detected."""
    model = _pbix(ADVENTURE_WORKS)
    taken = snap.take(SemanticGraph(model), "v1")
    relationships = [r for r in taken.objects.values() if r.kind == "relationship"]

    assert len(relationships) == len(model.relationships)
    # Each date role is a distinct snapshot entry rather than one overwriting another.
    date_roles = [r for r in relationships if "Sales[" in r.node_id and "Date[" in r.node_id]
    assert len(date_roles) == 3


def test_serialisation_is_deterministic_with_parallel_edges() -> None:
    """(source, target) is no longer unique, so the sort needs more than that."""
    graph = SemanticGraph(_pbix(ADVENTURE_WORKS))
    first = [(e["source"], e["target"], e.get("kind")) for e in graph.to_dict()["edges"]]
    second = [(e["source"], e["target"], e.get("kind")) for e in graph.to_dict()["edges"]]
    assert first == second


def test_documentation_describes_all_the_joins() -> None:
    from concordance.generate import document as doc
    from concordance.generate.requirements import Kind

    graph = SemanticGraph(_pbix(ADVENTURE_WORKS))
    markdown = doc.to_markdown(doc.build(graph, Kind.FUNCTIONAL))

    for column in ("OrderDateKey", "DueDateKey", "ShipDateKey"):
        assert column in markdown, f"{column} join missing from the FRD"

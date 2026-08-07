"""Objects the model declares but leaves undefined.

The deriver originally assumed every object was well-formed, and produced
high-confidence requirements for objects that define nothing:

* a measure with no expression became "shall be implemented exactly as the DAX
  expression recorded in the evidence" -- with no expression recorded;
* a hierarchy with no levels became "following the path ." and "shall contain 0
  levels in this order: .".

Both were asserted as *stated by the model* and reported as needing no review.
Documentation that confidently specifies something which does not exist is the
exact failure this project is built to prevent, so incomplete objects are now
low confidence and routed to a human.
"""

from __future__ import annotations

import pytest

from concordance.fingerprint import fingerprint_dax, fingerprint_parts, fingerprint_text
from concordance.generate import document as doc
from concordance.generate.requirements import Confidence, Kind, RequirementDeriver
from concordance.graph.csg import SemanticGraph
from concordance.model import Column, Hierarchy, Measure, SemanticModel, Table


def _model_with(**objects) -> SemanticGraph:
    model = SemanticModel(name="t", source_path="x", source_type="test")
    model.tables = [Table(name="T", fingerprint=fingerprint_text("T"))]
    for attribute, value in objects.items():
        setattr(model, attribute, value)
    return SemanticGraph(model)


def _incomplete(graph: SemanticGraph):
    return [
        r
        for r in RequirementDeriver(graph).derive()
        if r.category == "Incomplete definitions"
    ]


def test_measure_without_an_expression_is_not_asserted_as_fact() -> None:
    graph = _model_with(
        measures=[
            Measure(table="T", name="Broken", expression="", fingerprint=fingerprint_dax(""))
        ]
    )
    found = _incomplete(graph)

    assert len(found) == 1
    assert found[0].confidence is Confidence.LOW
    assert found[0].needs_review
    assert "carries no expression" in found[0].statement
    # The claim that used to be made must not appear anywhere.
    assert not any(
        "implemented exactly as the DAX expression" in r.statement
        for r in RequirementDeriver(graph).derive()
    )


def test_hierarchy_without_levels_does_not_produce_malformed_text() -> None:
    graph = _model_with(
        hierarchies=[
            Hierarchy(
                table="T",
                name="NoLevels",
                levels=(),
                fingerprint=fingerprint_parts("T", "NoLevels"),
            )
        ]
    )
    found = _incomplete(graph)

    assert len(found) == 1
    assert found[0].confidence is Confidence.LOW
    for requirement in RequirementDeriver(graph).derive():
        assert "following the path ." not in requirement.statement
        assert "0 levels in this order" not in requirement.statement


def test_calculated_column_without_an_expression_is_flagged() -> None:
    graph = _model_with(
        columns=[
            Column(
                table="T",
                name="BadCol",
                data_type="int64",
                expression="",
                fingerprint=fingerprint_dax(""),
            )
        ]
    )
    found = _incomplete(graph)

    assert len(found) == 1
    assert found[0].confidence is Confidence.LOW
    assert "carries no expression" in found[0].statement


def test_incomplete_objects_reach_the_review_queue_in_the_document() -> None:
    graph = _model_with(
        measures=[
            Measure(table="T", name="Broken", expression="", fingerprint=fingerprint_dax(""))
        ]
    )
    built = doc.build(graph, Kind.FUNCTIONAL)

    assert built.counts()["needs_review"] == 1
    markdown = doc.to_markdown(built)
    assert "## Awaiting human confirmation" in markdown
    assert "Incomplete definitions" in markdown


def test_well_formed_objects_are_unaffected() -> None:
    """The guard must not sweep up healthy measures."""
    expression = "SUM(T[Amount])"
    graph = _model_with(
        measures=[
            Measure(
                table="T",
                name="Good",
                expression=expression,
                fingerprint=fingerprint_dax(expression),
            )
        ]
    )
    assert _incomplete(graph) == []
    assert all(
        r.confidence is Confidence.HIGH
        for r in RequirementDeriver(graph).derive()
        if "Good" in r.statement
    )


@pytest.mark.parametrize("count,expected", [(1, "1 subject area:"), (2, "2 subject areas:")])
def test_scope_statement_pluralises(count: int, expected: str) -> None:
    model = SemanticModel(name="t", source_path="x", source_type="test")
    model.tables = [
        Table(name=f"T{i}", fingerprint=fingerprint_text(f"T{i}")) for i in range(count)
    ]
    scope = next(
        r for r in RequirementDeriver(SemanticGraph(model)).derive() if r.category == "Scope"
    )
    assert expected in scope.statement


# -- the shared measure-container rule ---------------------------------------

def test_measure_container_rule_is_shared_by_both_adapters() -> None:
    """The two formats express the same idea differently and must not diverge.

    A .pbix measure-only table has no columns at all; TMDL requires at least one,
    so modellers add a hidden placeholder. Both must reach the same answer.
    """
    from concordance.adapters.base import is_measure_container

    assert is_measure_container(has_measures=True, visible_columns=0)
    assert not is_measure_container(has_measures=True, visible_columns=3)
    # A table with neither columns nor measures is empty, not a container.
    assert not is_measure_container(has_measures=False, visible_columns=0)

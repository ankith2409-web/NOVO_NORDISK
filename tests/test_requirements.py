"""Requirement derivation and document generation.

The guarantees tested here are what make the generated document trustworthy:
requirements are derived from real objects rather than invented, identifiers
stay stable while implementations change, and confidence honestly separates
what the model states from what was inferred.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.fingerprint import fingerprint_dax, fingerprint_text
from concordance.generate import document as doc
from concordance.generate import patterns
from concordance.generate.requirements import (
    Confidence,
    Kind,
    RequirementDeriver,
)
from concordance.graph.csg import SemanticGraph
from concordance.model import Measure, SemanticModel, Table

MODELS = Path("data/models")


def _available(name: str) -> Path:
    path = MODELS / f"{name}.pbix"
    if not path.exists():
        pytest.skip(f"sample model not present: {path}")
    return path


@pytest.fixture(scope="module")
def sales_graph() -> SemanticGraph:
    return SemanticGraph(PbixAdapter().extract(str(_available("Sales_Returns_Sample"))))


def _single_measure_graph(expression: str) -> SemanticGraph:
    model = SemanticModel(name="t", source_path="x", source_type="pbix")
    model.tables = [Table(name="Sales", fingerprint=fingerprint_text("Sales"))]
    model.measures = [
        Measure(
            table="Sales",
            name="Net Sales",
            expression=expression,
            fingerprint=fingerprint_dax(expression),
        )
    ]
    return SemanticGraph(model)


# -- the binding contract ----------------------------------------------------

def test_requirement_id_survives_a_change_to_the_implementation() -> None:
    """Drift has to be reportable *against* a requirement, so its id must persist.

    If ids were derived from the expression, editing a measure would retire the
    old requirement and mint a new one -- and nothing would be flagged as having
    drifted, which defeats the entire mechanism.
    """
    baseline = _requirement_for('CALCULATE(SUM(Sales[Amount]),Sales[Status]="Sold")')
    changed = _requirement_for('CALCULATE(SUM(Sales[Amount]),Sales[Status]="Returned")')

    assert baseline.id == changed.id
    assert baseline.evidence[0].fingerprint != changed.evidence[0].fingerprint


def test_reformatting_changes_neither_id_nor_binding() -> None:
    baseline = _requirement_for('CALCULATE(SUM(Sales[Amount]),Sales[Status]="Sold")')
    tidied = _requirement_for(
        '  CALCULATE( SUM(Sales[Amount]) , Sales[Status]="Sold" ) // reformatted'
    )
    assert baseline.id == tidied.id
    assert baseline.evidence[0].fingerprint == tidied.evidence[0].fingerprint


def _requirement_for(expression: str):
    graph = _single_measure_graph(expression)
    return next(
        r
        for r in RequirementDeriver(graph).derive()
        if r.kind is Kind.FUNCTIONAL and "Net Sales" in r.statement
    )


def test_every_requirement_is_bound_to_something_real(sales_graph: SemanticGraph) -> None:
    """No requirement may reference a node that is not in the graph.

    This is the guarantee that a requirement cannot be invented: it exists only
    because an extracted object exists.
    """
    node_ids = set(sales_graph.graph.nodes)
    for requirement in RequirementDeriver(sales_graph).derive():
        for evidence in requirement.evidence:
            if "->" in evidence.node_id:  # a relationship, expressed as an edge
                continue
            assert evidence.node_id in node_ids, requirement.id


def test_requirement_ids_are_unique(sales_graph: SemanticGraph) -> None:
    requirements = RequirementDeriver(sales_graph).derive()
    ids = [r.id for r in requirements]
    assert len(ids) == len(set(ids))


def test_derivation_is_deterministic(sales_graph: SemanticGraph) -> None:
    first = [(r.id, r.statement) for r in RequirementDeriver(sales_graph).derive()]
    second = [(r.id, r.statement) for r in RequirementDeriver(sales_graph).derive()]
    assert first == second


# -- confidence is honest ----------------------------------------------------

def test_a_measure_is_stated_but_a_join_is_inferred(sales_graph: SemanticGraph) -> None:
    """The model states what a measure computes; it never states why a join exists."""
    requirements = RequirementDeriver(sales_graph).derive()

    metrics = [r for r in requirements if r.category == "Metrics and KPIs"]
    assert metrics and all(r.confidence is Confidence.HIGH for r in metrics)

    analysis = [
        r
        for r in requirements
        if r.category == "Dimensional analysis" and "shall be able to analyse" in r.statement
    ]
    assert analysis and all(r.confidence is Confidence.MEDIUM for r in analysis)


def test_inactive_relationship_is_routed_to_human_review(
    sales_graph: SemanticGraph,
) -> None:
    """Its purpose genuinely is not recorded anywhere in the model."""
    flagged = [r for r in RequirementDeriver(sales_graph).derive() if r.needs_review]
    assert flagged
    assert any("inactive relationship" in r.statement for r in flagged)


def test_only_low_confidence_requires_review(sales_graph: SemanticGraph) -> None:
    for requirement in RequirementDeriver(sales_graph).derive():
        assert requirement.needs_review == (requirement.confidence is Confidence.LOW)


# -- pattern recognition ------------------------------------------------------

@pytest.mark.parametrize(
    "expression,expected",
    [
        ("CALCULATE([S], PREVIOUSMONTH('Calendar'[Date]))", "time_intelligence"),
        ("RANKX(ALL('Product'[Product]), [Net Sales],, DESC)", "ranking"),
        ("DIVIDE([A], [B], 0)", "ratio"),
        ("CALCULATE([S], USERELATIONSHIP(Sales[Date], 'Date'[Date]))", "relationship_override"),
        ("DISTINCTCOUNT(Sales[CustomerID])", "distinct_count"),
        ("SUM(Sales[Amount])", "aggregation"),
    ],
)
def test_dax_patterns_are_recognised(expression: str, expected: str) -> None:
    assert patterns.primary(expression) is not None
    assert any(p.key == expected for p in patterns.detect(expression))


def test_a_function_name_inside_a_string_is_not_a_call() -> None:
    """Pattern detection runs on the lexer, so text cannot fake a function call."""
    assert patterns.detect('IF(T[Note] = "we use RANKX here", 1, 0)') and not any(
        p.key == "ranking" for p in patterns.detect('IF(T[Note] = "we use RANKX here", 1, 0)')
    )


def test_real_measure_gets_the_right_pattern(sales_graph: SemanticGraph) -> None:
    """Net Sales PM uses PREVIOUSMONTH in the actual model."""
    measure = next(m for m in sales_graph.model.measures if m.name == "Net Sales PM")
    assert any(p.key == "time_intelligence" for p in patterns.detect(measure.expression))


# -- documents ----------------------------------------------------------------

def test_brd_and_frd_split_requirements_without_overlap(
    sales_graph: SemanticGraph,
) -> None:
    brd = doc.build(sales_graph, Kind.BUSINESS)
    frd = doc.build(sales_graph, Kind.FUNCTIONAL)

    assert brd.requirements and frd.requirements
    assert not {r.id for r in brd.requirements} & {r.id for r in frd.requirements}

    total = len(RequirementDeriver(sales_graph).derive())
    assert len(brd.requirements) + len(frd.requirements) == total


def test_markdown_contains_a_traceability_row_per_requirement(
    sales_graph: SemanticGraph,
) -> None:
    built = doc.build(sales_graph, Kind.BUSINESS)
    markdown = doc.to_markdown(built)

    assert "## Traceability matrix" in markdown
    for requirement in built.requirements:
        assert requirement.id in markdown


def test_review_queue_is_surfaced_at_the_top_of_the_document(
    sales_graph: SemanticGraph,
) -> None:
    built = doc.build(sales_graph, Kind.BUSINESS)
    markdown = doc.to_markdown(built)

    assert built.review_queue
    heading = markdown.index("## Awaiting human confirmation")
    first_section = markdown.index("## 1. ")
    assert heading < first_section, "review queue must precede the requirements"


def test_coverage_gaps_become_documented_gap_requirements() -> None:
    """A model feature the extractor cannot read must appear in the document."""
    from concordance.model import CoverageGap

    model = SemanticModel(name="t", source_path="x", source_type="pbix")
    model.tables = [Table(name="Sales", fingerprint=fingerprint_text("Sales"))]
    model.coverage_gaps = [CoverageGap(feature="KPI objects", count=3, reason="not extracted")]

    requirements = RequirementDeriver(SemanticGraph(model)).derive()
    gaps = [r for r in requirements if r.category == "Documented gaps"]

    assert len(gaps) == 1
    assert "3 KPI objects" in gaps[0].statement
    assert gaps[0].needs_review, "an unreadable feature must not pass silently"


@pytest.mark.parametrize("name", ["Sales_Returns_Sample", "AdventureWorks_Sales", "Supply_Chain_Sample"])
def test_documents_generate_for_every_sample_model(name: str) -> None:
    graph = SemanticGraph(PbixAdapter().extract(str(_available(name))))
    for kind in (Kind.BUSINESS, Kind.FUNCTIONAL):
        built = doc.build(graph, kind)
        markdown = doc.to_markdown(built)
        assert built.requirements, f"{name} produced no {kind.value} requirements"
        assert markdown.startswith("# ")

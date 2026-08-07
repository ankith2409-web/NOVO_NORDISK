"""Whole-table references in DAX.

Found by auditing which measures had no recorded dependency at all: three of the
clinical model's base measures -- the ones every other metric builds on --
appeared to depend on nothing, because ``COUNTROWS(Patient)`` names a table
rather than a column and nothing was looking for that.

Left unfixed, changing a source table looks like it affects nothing, which is
precisely the question impact analysis exists to answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.adapters.tmdl import TmdlAdapter
from concordance.graph.csg import SemanticGraph, measure_id, table_id
from concordance.normalize.dax import extract_references

CLINICAL = Path("data/models/ClinicalTrialSafety.SemanticModel")


def _clinical() -> SemanticGraph:
    if not CLINICAL.exists():
        pytest.skip(f"TMDL model not present: {CLINICAL}")
    return SemanticGraph(TmdlAdapter().extract(str(CLINICAL)))


# -- what counts as a table reference ---------------------------------------

@pytest.mark.parametrize(
    "expression,expected",
    [
        ("COUNTROWS(Patient)", {"Patient"}),
        ("CALCULATE([S], REMOVEFILTERS(Product))", {"Product"}),
        ("SUMX(Sales, Sales[Qty] * Sales[Price])", {"Sales"}),
        ("COUNTROWS('Clinical Metrics')", {"Clinical Metrics"}),
    ],
)
def test_whole_table_references_are_found(expression: str, expected: set) -> None:
    assert expected <= set(extract_references(expression).table_candidates)


@pytest.mark.parametrize(
    "expression,not_expected",
    [
        # A qualifier belongs to the column reference, not to a table read.
        ("RANKX(ALL('Product'[Product]), [S])", "Product"),
        # Function names are calls, not tables.
        ("IF([X] > 1, TRUE(), FALSE())", "IF"),
        # A VAR name is local to the expression.
        ("VAR total = SUM(T[A])\nRETURN total * 2", "total"),
    ],
)
def test_things_that_are_not_table_references(expression: str, not_expected: str) -> None:
    candidates = {c.casefold() for c in extract_references(expression).table_candidates}
    assert not_expected.casefold() not in candidates


def test_candidates_are_filtered_against_real_table_names() -> None:
    """Keywords survive as candidates; only resolution decides what is real.

    This is why the field is named *candidates*: the lexer cannot know what a
    table is, so it is deliberately permissive and the model has the final say.
    """
    candidates = extract_references("VAR x = SUM(T[A])\nRETURN x").table_candidates
    assert "VAR" in candidates or "RETURN" in candidates

    graph = _clinical()
    for measure in graph.model.measures:
        for name in measure.depends_on_tables:
            assert name in {t.name for t in graph.model.tables}


# -- the effect on the real models -------------------------------------------

def test_no_clinical_measure_is_left_without_any_dependency() -> None:
    graph = _clinical()
    orphans = [
        m.name
        for m in graph.model.measures
        if not m.depends_on_columns
        and not m.depends_on_measures
        and not m.depends_on_tables
    ]
    assert orphans == []


def test_base_measures_depend_on_their_source_table() -> None:
    graph = _clinical()
    expected = {
        "Patients Enrolled": "Patient",
        "Adverse Event Count": "AdverseEvent",
        "Protocol Deviations": "ProtocolDeviation",
    }
    for measure_name, table in expected.items():
        measure = next(m for m in graph.model.measures if m.name == measure_name)
        assert table in measure.depends_on_tables, measure_name


def test_changing_a_table_surfaces_every_metric_built_on_it() -> None:
    """The question impact analysis exists to answer."""
    graph = _clinical()
    affected = graph.dependents_of(table_id("Patient"))

    assert measure_id("Clinical Metrics", "Patients Enrolled") in affected
    # And transitively, everything derived from it.
    assert measure_id("Clinical Metrics", "Dropout Rate") in affected
    assert measure_id("Clinical Metrics", "Enrollment Rate") in affected


def test_pbix_models_pick_up_whole_table_reads_too() -> None:
    """ALL('Calendar') in the Sales & Returns model is a genuine table read."""
    path = Path("data/models/Sales_Returns_Sample.pbix")
    if not path.exists():
        pytest.skip(f"sample model not present: {path}")

    model = PbixAdapter().extract(str(path))
    measure = next(
        m for m in model.measures if m.name == "WIF Units Returned Average"
    )
    assert "Calendar" in measure.depends_on_tables


def test_string_literals_cannot_fake_a_table_reference() -> None:
    refs = extract_references('IF(T[C] = "Patient", 1, 0)')
    assert "Patient" not in refs.table_candidates

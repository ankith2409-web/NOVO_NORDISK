"""Extraction and graph tests, run against the real sample models.

Nothing here uses a fixture or a stub model: every assertion is against the
actual .pbix files in data/models, so a regression in the parser surfaces as a
test failure rather than as wrong documentation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.fingerprint import fingerprint_dax
from concordance.graph.csg import SemanticGraph, measure_id

MODELS = Path("data/models")
SAMPLES = ["Sales_Returns_Sample", "AdventureWorks_Sales", "Supply_Chain_Sample"]


def _available(name: str) -> Path:
    path = MODELS / f"{name}.pbix"
    if not path.exists():
        pytest.skip(f"sample model not present: {path}")
    return path


@pytest.fixture(scope="module")
def sales_graph() -> SemanticGraph:
    return SemanticGraph(PbixAdapter().extract(str(_available("Sales_Returns_Sample"))))


# -- generality across all three models -------------------------------------

@pytest.mark.parametrize("name", SAMPLES)
def test_every_sample_model_extracts(name: str) -> None:
    """The parser must generalise, not be tuned to one favourite model."""
    model = PbixAdapter().extract(str(_available(name)))
    assert model.tables, "no tables extracted"
    assert model.columns, "no columns extracted"
    summary = model.summary()
    assert summary["user_tables"] > 0


@pytest.mark.parametrize("name", SAMPLES)
def test_every_measure_and_calculated_column_is_fingerprinted(name: str) -> None:
    model = PbixAdapter().extract(str(_available(name)))
    for measure in model.measures:
        assert len(measure.fingerprint) == 64, measure.qualified_name
    for column in model.columns:
        assert len(column.fingerprint) == 64, column.qualified_name


@pytest.mark.parametrize("name", SAMPLES)
def test_graph_builds_and_every_object_becomes_a_node(name: str) -> None:
    model = PbixAdapter().extract(str(_available(name)))
    graph = SemanticGraph(model)
    stats = graph.stats()
    assert stats["nodes"] >= len(model.tables) + len(model.columns) + len(model.measures)
    assert graph.fingerprints()


# -- the specific content of Sales & Returns --------------------------------

def test_known_tables_are_present(sales_graph: SemanticGraph) -> None:
    names = {t.name for t in sales_graph.model.tables}
    assert {"Sales", "Product", "Customer", "Store", "Calendar"} <= names


def test_auto_generated_date_tables_are_flagged_as_system(sales_graph: SemanticGraph) -> None:
    system = [t for t in sales_graph.model.tables if t.is_system]
    assert system, "expected Power BI's hidden date tables to be detected"
    assert all(
        t.name.startswith(("DateTableTemplate_", "LocalDateTable_")) for t in system
    )
    assert all(not t.is_system for t in sales_graph.model.tables if t.name == "Sales")


def test_net_sales_is_extracted_with_its_real_expression(sales_graph: SemanticGraph) -> None:
    measure = next(m for m in sales_graph.model.measures if m.name == "Net Sales")
    assert "CALCULATE" in measure.expression.upper()
    assert ("Sales", "Amount") in measure.depends_on_columns


def test_measure_to_measure_dependencies_are_resolved(sales_graph: SemanticGraph) -> None:
    """'Last 2 Months Net Sales' is [Net Sales] + [Net Sales PM] in the real model."""
    measure = next(
        m for m in sales_graph.model.measures if m.name == "Last 2 Months Net Sales"
    )
    assert {"Net Sales", "Net Sales PM"} <= set(measure.depends_on_measures)


def test_transitive_dependencies_reach_through_intermediate_measures(
    sales_graph: SemanticGraph,
) -> None:
    node = measure_id("Analysis DAX", "Last 2 Months Net Sales")
    deps = sales_graph.dependencies_of(node)
    # via [Net Sales PM] -> [Net Sales] -> Sales[Amount]
    assert any(d.endswith("Sales[Amount]") for d in deps), deps


def test_changing_a_measure_is_visible_to_its_dependents(
    sales_graph: SemanticGraph,
) -> None:
    """Impact analysis: what breaks if Net Sales changes."""
    dependents = sales_graph.dependents_of(measure_id("Analysis DAX", "Net Sales"))
    assert len(dependents) > 1, "Net Sales underpins several measures in this model"


def test_relationships_carry_cardinality_and_direction(sales_graph: SemanticGraph) -> None:
    joins = sales_graph.join_paths()
    assert joins
    sales_to_product = [
        j for j in joins if j["from"] == "Sales" and j["to"] == "Product"
    ]
    assert sales_to_product, "the Sales -> Product join should be present"
    assert sales_to_product[0]["cardinality"] == "M:1"


def test_inactive_relationships_are_preserved_not_dropped(sales_graph: SemanticGraph) -> None:
    """An inactive join still exists in the model and belongs in the documentation."""
    assert any(not j["is_active"] for j in sales_graph.join_paths())


def test_extraction_is_deterministic() -> None:
    """Two runs of the same file must produce identical fingerprints."""
    path = str(_available("Sales_Returns_Sample"))
    first = SemanticGraph(PbixAdapter().extract(path)).fingerprints()
    second = SemanticGraph(PbixAdapter().extract(path)).fingerprints()
    assert first == second


def test_fingerprint_of_a_real_measure_survives_reformatting(
    sales_graph: SemanticGraph,
) -> None:
    """The week-1 acceptance test, on real model content rather than a toy string."""
    measure = next(m for m in sales_graph.model.measures if m.name == "Net Sales")
    reformatted = f"\n// quarterly pack\n{measure.expression}\n"
    assert fingerprint_dax(reformatted) == measure.fingerprint


def test_serialisation_round_trips_to_json() -> None:
    import json

    graph = SemanticGraph(PbixAdapter().extract(str(_available("Supply_Chain_Sample"))))
    payload = json.loads(json.dumps(graph.to_dict(), default=str))
    assert payload["stats"]["nodes"] > 0
    assert payload["model"]["source_type"] == "pbix"


def test_missing_file_raises_a_clear_error() -> None:
    with pytest.raises(FileNotFoundError):
        PbixAdapter().extract("data/models/does_not_exist.pbix")

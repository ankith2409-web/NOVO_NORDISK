"""Hierarchy extraction and coverage reporting.

This gap was found by auditing what PBIXRay exposes against what the adapter
actually reads, rather than by any failing test -- the extractor was silently
blind to every hierarchy in every model, and nothing said so.

The coverage tests matter as much as the hierarchy ones. None of the three
sample models contains KPIs, row-level security or calculation groups, so the
reporting path cannot be exercised by real data and is tested against a
synthetic source instead. An untested safety net is not a safety net.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.fingerprint import fingerprint_parts
from concordance.graph.csg import SemanticGraph, column_id, hierarchy_id

MODELS = Path("data/models")
SAMPLES = ["Sales_Returns_Sample", "AdventureWorks_Sales", "Supply_Chain_Sample"]


def _available(name: str) -> Path:
    path = MODELS / f"{name}.pbix"
    if not path.exists():
        pytest.skip(f"sample model not present: {path}")
    return path


@pytest.fixture(scope="module")
def adventure_works() -> SemanticGraph:
    """The only sample with hierarchies on user tables."""
    return SemanticGraph(PbixAdapter().extract(str(_available("AdventureWorks_Sales"))))


# -- extraction completeness -------------------------------------------------

@pytest.mark.parametrize("name", SAMPLES)
def test_every_hierarchy_and_level_in_the_source_is_extracted(name: str) -> None:
    """Counted against PBIXRay's own tables, not against a hand-written number."""
    from pbixray import PBIXRay

    path = str(_available(name))
    raw = PBIXRay(path)
    model = PbixAdapter().extract(path)

    assert len(model.hierarchies) == len(raw.tmschema_hierarchies)
    assert sum(len(h.levels) for h in model.hierarchies) == len(raw.tmschema_levels)


@pytest.mark.parametrize("name", SAMPLES)
def test_level_order_matches_the_source_ordinal(name: str) -> None:
    """Drill order is meaning: Year -> Month is not Month -> Year."""
    from pbixray import PBIXRay

    path = str(_available(name))
    raw = PBIXRay(path)
    levels = raw.tmschema_levels

    for hierarchy in PbixAdapter().extract(path).hierarchies:
        rows = levels[
            (levels.TableName == hierarchy.table)
            & (levels.HierarchyName == hierarchy.name)
        ].sort_values("Ordinal")
        assert [lv.name for lv in hierarchy.levels] == list(rows.Name), (
            hierarchy.qualified_name
        )


def test_known_business_hierarchies_are_present(adventure_works: SemanticGraph) -> None:
    by_name = {h.qualified_name: h for h in adventure_works.model.hierarchies}

    products = by_name["Product[Products]"]
    assert products.path == "Category -> Subcategory -> Model -> Product"

    geography = by_name["Customer[Geography]"]
    assert geography.levels[0].name == "Country-Region"
    assert len(geography.levels) == 5


def test_system_date_hierarchies_are_separated_from_user_ones(
    adventure_works: SemanticGraph,
) -> None:
    """4 of the 10 sit on auto-generated date tables and are not business content."""
    model = adventure_works.model
    assert len(model.hierarchies) == 10
    assert len(model.user_hierarchies()) == 6
    assert all("Date Hierarchy" != h.name for h in model.user_hierarchies())


# -- graph wiring ------------------------------------------------------------

def test_hierarchy_becomes_a_node_bound_to_its_columns(
    adventure_works: SemanticGraph,
) -> None:
    node = hierarchy_id("Product", "Products")
    assert node in adventure_works.graph

    deps = adventure_works.dependencies_of(node)
    for column in ("Category", "Subcategory", "Model", "Product"):
        assert column_id("Product", column) in deps


def test_changing_a_column_surfaces_the_hierarchy_that_uses_it(
    adventure_works: SemanticGraph,
) -> None:
    """Impact analysis has to reach hierarchies, or a drill path breaks silently."""
    affected = adventure_works.dependents_of(column_id("Product", "Category"))
    assert hierarchy_id("Product", "Products") in affected


def test_adding_hierarchies_introduced_no_unresolved_references(
    adventure_works: SemanticGraph,
) -> None:
    assert adventure_works.unresolved == []


# -- fingerprint semantics ---------------------------------------------------

def test_reordering_levels_changes_the_fingerprint() -> None:
    """A different drill path is a different hierarchy, even with the same levels."""
    forward = fingerprint_parts("T", "H", "0:Year:Year", "1:Quarter:Quarter")
    reversed_ = fingerprint_parts("T", "H", "0:Quarter:Quarter", "1:Year:Year")
    assert forward != reversed_


def test_hierarchy_fingerprints_are_stable_across_extractions() -> None:
    path = str(_available("AdventureWorks_Sales"))
    first = {h.qualified_name: h.fingerprint for h in PbixAdapter().extract(path).hierarchies}
    second = {h.qualified_name: h.fingerprint for h in PbixAdapter().extract(path).hierarchies}
    assert first == second


# -- coverage reporting ------------------------------------------------------

class _RawWithUnextractedFeatures:
    """Stands in for a model using features none of the samples contain."""

    tmschema_kpis = pd.DataFrame([{"Name": "Revenue KPI"}, {"Name": "Margin KPI"}])
    rls = pd.DataFrame([{"Role": "Sales EMEA"}])
    ols = pd.DataFrame([])
    tmschema_calculation_groups = pd.DataFrame([{"Name": "Time Intelligence"}])
    tmschema_calculation_items = pd.DataFrame([])
    tmschema_perspectives = None


def test_unextracted_features_are_reported_when_present() -> None:
    gaps = PbixAdapter()._coverage_gaps(_RawWithUnextractedFeatures())
    reported = {g.feature: g.count for g in gaps}

    assert reported["KPI objects"] == 2
    assert reported["row-level security roles"] == 1
    assert reported["calculation groups"] == 1
    # Empty and absent tables are not gaps -- nothing was lost.
    assert "object-level security" not in reported
    assert "perspectives" not in reported


@pytest.mark.parametrize("name", SAMPLES)
def test_sample_models_report_no_coverage_gaps(name: str) -> None:
    """These three genuinely have no KPIs, RLS or calculation groups."""
    assert PbixAdapter().extract(str(_available(name))).coverage_gaps == []


def test_coverage_gaps_reach_the_serialised_graph(adventure_works: SemanticGraph) -> None:
    """Whatever consumes the graph downstream must be able to see the gaps."""
    payload = adventure_works.to_dict()
    assert "coverage_gaps" in payload
    assert payload["stats"]["coverage_gaps"] == 0

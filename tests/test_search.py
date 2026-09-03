"""One question, asked once, answered across the whole model.

The interface had six pages and no way to look in all of them at once. Finding a
measure meant knowing that measures live on the dataset page and not the
dashboard page; finding the tile that shows it meant knowing the reverse. That
is a question about this tool's furniture, and nobody opening a documentation
tool came to answer one.

The tests below are mostly about *ranking*, because a search that returns the
right forty things in the wrong order is a search somebody scrolls instead of
using.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.graph.csg import SemanticGraph
from concordance.web.search import search

STORE = Path("data/models/StoreSales.pbix")


@pytest.fixture(scope="module")
def graph():
    if not STORE.exists():
        pytest.skip(f"model not present: {STORE}")
    return SemanticGraph(PbixAdapter().extract(str(STORE)))


def _names(result) -> list[str]:
    return [hit["name"] for hit in result["results"]]


def _kinds(result) -> list[str]:
    return [hit["kind"] for hit in result["results"]]


def test_nothing_is_searched_for_nothing(graph) -> None:
    """An empty box returns an empty list, not the whole model."""
    assert search(graph, "")["results"] == []
    assert search(graph, "   ")["results"] == []


def test_every_kind_of_object_is_reachable(graph) -> None:
    """Tables, columns, measures, hierarchies and tiles, from one box."""
    found = {
        "table": search(graph, "Fiscal calendar"),
        "column": search(graph, "SellingAreaSize"),
        "measure": search(graph, "Total Sales Variance"),
        "hierarchy": search(graph, "Fiscal Hierarchy"),
    }
    for kind, result in found.items():
        assert kind in _kinds(result), f"nothing of kind {kind} for that query"

    # And the report layer, which is the half a reader points at.
    tiles = search(graph, "Sales")
    assert {"tile", "kpi"} & set(_kinds(tiles)), "no tile reachable by name"


def test_an_exact_name_outranks_a_longer_one_containing_it(graph) -> None:
    """Typing the whole name of a thing should not bury it under its neighbours.

    `Sales` is a measure in this model, and so are `Sales LY`, `Last Year Sales`
    and nine others with the word in them. Sorted by name alone the exact match
    lands eighth.
    """
    names = _names(search(graph, "Sales"))
    assert names[0] == "Sales"
    assert "Last Year Sales" in names


def test_a_name_that_starts_with_the_query_beats_one_that_merely_contains_it(graph) -> None:
    names = _names(search(graph, "Sales"))
    leading = names.index("Sales LY")
    contains = names.index("Last Year Sales")
    assert leading < contains, f"ordering was {names}"


def test_a_formula_match_is_found_but_ranked_last(graph) -> None:
    """"Which of these divides by CALCULATE" is a real question a developer has.

    It is also never as good a hit as a name, so it sits in its own tier below
    every name match rather than mixed in with them.
    """
    result = search(graph, "CALCULATE")
    names = _names(result)
    assert names, "no measure mentions CALCULATE, so this proves nothing"
    for hit in result["results"]:
        # Every hit here was found in an expression, so none of them should
        # have CALCULATE in the name -- and if one did it would come first.
        assert "calculate" not in hit["name"].casefold()

    # A query that matches both ways puts every name match first. `Sales`
    # appears in eleven measure names and in the body of several more.
    both = search(graph, "Sales")["results"]
    by_name = [h for h in both if "sales" in h["name"].casefold()]
    assert by_name, "expected name matches for 'Sales'"
    assert both[: len(by_name)] == by_name, "a formula match jumped a name match"


def test_a_measure_outranks_a_column_of_the_same_name(graph) -> None:
    """`Date` is a table, a column on it, and part of two hierarchies.

    When several kinds match equally well the more central one leads: somebody
    typing a name is far more often after the measure or the table than after
    one of the model's several hundred columns.
    """
    kinds = _kinds(search(graph, "Date"))
    assert kinds.index("table") < kinds.index("column")


def test_each_hit_says_where_to_open_it(graph) -> None:
    """A result the caller cannot act on is a result that needs a second lookup."""
    for hit in search(graph, "sales")["results"]:
        assert hit["view"] in {"dataset", "model", "dashboard", "requirements"}
        assert hit["target"], f"{hit['name']} says where to look but not what to open"


def test_a_kpi_is_labelled_by_the_same_rule_the_dashboard_uses(graph) -> None:
    """Not a second definition of "KPI" written for the search box.

    Two definitions in one product is one more than the number that can be
    right, and these two answers sit on adjacent screens.
    """
    from concordance.generate.tiles import correlate

    dashboard = {
        tile.title for page in correlate(graph) for tile in page.tiles if tile.is_kpi
    }
    assert dashboard, "the sample should have KPIs for this to compare against"

    for title in dashboard:
        hits = [h for h in search(graph, title)["results"] if h["name"] == title]
        assert any(h["kind"] == "kpi" for h in hits), (
            f"{title} is a KPI on the dashboard and not in search"
        )


def test_the_search_is_case_and_position_insensitive(graph) -> None:
    assert _names(search(graph, "SALES"))[0] == "Sales"
    assert "Total Sales Variance" in _names(search(graph, "otal sal"))


def test_a_long_result_set_is_capped_and_says_so(graph) -> None:
    """Forty is enough to fill the palette twice; a whole model is not a result."""
    result = search(graph, "a", limit=5)
    assert len(result["results"]) == 5
    assert result["truncated"] is True
    assert search(graph, "Fiscal Hierarchy")["truncated"] is False

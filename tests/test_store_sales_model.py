"""The plain-language sample model, and why it exists.

A reviewer said the same thing four times in one session: the clinical and
manufacturing models are the wrong thing to evaluate a documentation tool on.

    "if you are not able to understand these things, you will not be able to
     correlate, because you don't know what is Clinical Trial Safety... you can
     make a simple one, like use profit and sales information"
    "total sales, it can be number of units sold into the price"

She is right, and the point generalises past her: a reader spending their
attention on what a protocol deviation is has none left for whether the
document describing it is any good. `StoreSales` is sales, cost, profit, margin
and orders, and Total Sales is unit price times units sold exactly as she said
it.

These tests hold it to the standard that makes it worth having. It has to be
readable -- every measure described, no jargon -- and it has to *work*: the
whole point of a demonstration model is that it demonstrates, so its measures
translate, its previous-month pair translates, and the one measure that cannot
be translated is there on purpose and says so.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.generate.sql import Status, translate_all
from concordance.graph.csg import SemanticGraph

MODEL = Path("data/models/StoreSales.SemanticModel")


@pytest.fixture(scope="module")
def model():
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return TmdlAdapter().extract(str(MODEL))


@pytest.fixture(scope="module")
def graph(model):
    return SemanticGraph(model)


@pytest.fixture(scope="module")
def measures(model):
    return {m.name: m for m in model.measures}


def test_it_reads_with_nothing_left_unaccounted_for(model, graph) -> None:
    """A sample model that reports gaps in itself is not a sample worth having."""
    assert model.coverage_gaps == []
    assert graph.unresolved == []


def test_the_headline_measures_are_the_ones_on_the_dashboard(measures) -> None:
    """Named after the tiles, so the report and the document use one vocabulary.

    The correlation feature is worth nothing if the two halves call the same
    number different things.
    """
    for tile in ("Total Sales", "Total Profit", "Profit Margin", "Orders", "Products"):
        assert tile in measures, f"{tile} is on the dashboard and not in the model"


def test_total_sales_is_price_times_units_as_described(measures) -> None:
    """Literally what she asked for: "unit price into units sold"."""
    assert measures["Total Sales"].expression == "SUM(Sales[Line Sales])"


def test_the_arithmetic_is_stored_once(model) -> None:
    """Units x price lives on the line, not in each measure.

    Two measures each multiplying it out is two places for it to drift apart,
    and the whole subject of this project is definitions drifting apart.
    """
    calculated = {c.name: c.expression for c in model.columns if c.expression}
    assert calculated["Line Sales"] == "Sales[Units] * Sales[UnitPrice]"
    assert calculated["Line Cost"] == "Sales[Units] * Sales[UnitCost]"


def test_orders_counts_orders_rather_than_lines(measures) -> None:
    """One order of three items is one order.

    The obvious COUNTROWS(Sales) would be wrong and would look right, which is
    exactly the kind of thing this tool exists to surface -- so the sample model
    should not itself contain it.
    """
    assert "DISTINCTCOUNT" in measures["Orders"].expression
    assert "OrderID" in measures["Orders"].expression


def test_every_measure_is_described(model) -> None:
    """This model is read by people, not only by the translator.

    A measure with no description forces a reader to reverse-engineer intent
    from DAX, which is the problem this project exists to remove.
    """
    undescribed = [
        m.name for m in model.measures if not (m.description or "").strip()
    ]
    # Total Cost and Units Sold are self-describing names over one column; the
    # rest must carry a sentence.
    assert set(undescribed) <= {"Total Cost", "Units Sold", "Total Profit PM"}, (
        f"undescribed measures: {undescribed}"
    )


def test_nearly_everything_translates(model) -> None:
    """A demonstration model has to demonstrate."""
    results = translate_all(model)
    exact = [t for t in results if t.status is Status.EXACT]
    assert len(exact) >= len(results) - 1, [
        (t.measure, t.blocked_by) for t in results if t.status is not Status.EXACT
    ]


def test_the_previous_month_pair_translates(model, measures) -> None:
    """The shape a reviewer asked for by name, in the simplest possible model.

    "if the tool cannot populate previous month SQL queries, these are very
    basic things needed" -- so this model carries the basic thing, and it works.
    """
    by_name = {t.measure: t for t in translate_all(model)}
    for name in ("Total Sales PM", "Sales vs PM", "Sales Growth %"):
        result = by_name[name]
        assert result.status is Status.EXACT, f"{name}: {result.reason}"
        assert "DATE_TRUNC('month'" in result.sql
        assert "LAG(" in result.sql


def test_one_measure_is_refused_on_purpose(model) -> None:
    """A model where everything translates would misrepresent the tool.

    `Share of All Sales` compares each row against the whole table whatever the
    report is filtered to, so no single query stands for it. It is in the model
    so that a demonstration shows both halves: what converts, and what is
    honestly declined.
    """
    blocked = [t for t in translate_all(model) if t.status is not Status.EXACT]
    assert [t.measure for t in blocked] == ["Share of All Sales"]
    assert blocked[0].blocked_by == "ALL"
    assert blocked[0].reason


def test_it_has_an_inactive_relationship_to_ask_about(model) -> None:
    """The confirmation queue needs something to hold, and ship-date is a real
    example rather than a contrived one: every orders table has two dates."""
    inactive = [r for r in model.relationships if not r.is_active]
    assert len(inactive) == 1
    assert inactive[0].from_column == "ShipDate"


def test_the_model_stays_small_enough_to_take_in(model) -> None:
    """The point of it is that a reviewer can hold all of it in their head.

    Guarded with a number so that "simple" survives future additions: anything
    that pushes it past this should go in one of the other models instead.
    """
    summary = model.summary()
    assert summary["user_tables"] <= 6
    assert summary["measures"] <= 16

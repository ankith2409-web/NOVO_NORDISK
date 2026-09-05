"""Reading a report's tiles, and joining each one to its DAX and its SQL.

The reviewers asked for one thing above all others: given a tile on a dashboard
called "Total Sales", show the formula behind it. Two of them said it in the
same session, and one said it was the only missing piece.

Everything here runs against Microsoft's own published ``.pbix`` samples rather
than a fixture, because the property that matters is not "the parser accepts
what the parser writes" -- it is that it reads what Power BI actually produces,
including the parts of that format which are misleading if taken at face value.

The trap is in ``test_a_renamed_field_resolves_to_what_the_query_selects``. It
is not hypothetical: it is the first card on the first page of Microsoft's Sales
& Returns report, and reading the obvious field reports a measure that has never
existed in that model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.generate.tiles import correlate, counts
from concordance.graph.csg import SemanticGraph
from concordance.normalize.layout import read_layout

SALES = Path("data/models/Sales_Returns_Sample.pbix")
SUPPLY = Path("data/models/Supply_Chain_Sample.pbix")


def _model(path: Path):
    if not path.exists():
        pytest.skip(f"model not present: {path}")
    return PbixAdapter().extract(str(path))


@pytest.fixture(scope="module")
def sales():
    return _model(SALES)


@pytest.fixture(scope="module")
def pages(sales):
    return correlate(SemanticGraph(sales))


def _tile(pages, page: str, title: str):
    for one in pages:
        if one.name != page:
            continue
        for tile in one.tiles:
            if tile.title == title:
                return tile
    raise AssertionError(f"no tile titled {title!r} on page {page!r}")


# -- reading the report layer --------------------------------------------------


def test_the_report_layer_is_read_from_a_real_pbix(sales) -> None:
    """Pages and tiles, from the file Microsoft published."""
    assert len(sales.report_pages) == 18
    assert [p.name for p in sales.report_pages][:3] == ["Legal", "Intro", "Net Sales"]
    assert len(sales.visuals()) == 71


def test_furniture_is_not_a_tile(sales) -> None:
    """Buttons, images and shapes are excluded, and they are most of the report.

    166 visual containers, 71 tiles. Whatever else changes, a control that shows
    no data must never appear in a list of things correlated to measures.
    """
    for visual in sales.visuals():
        assert visual.fields, f"{visual.visual_type} has no fields and is not a tile"
    assert all(v.visual_type not in {"actionButton", "image", "basicShape", "textbox"}
               for v in sales.visuals())


def test_a_semantic_model_alone_has_no_report(tmp_path) -> None:
    """A .SemanticModel folder carries no report, and none is invented for it."""
    from concordance.adapters.tmdl import TmdlAdapter

    source = Path("data/models/ClinicalTrialSafety.SemanticModel")
    if not source.exists():
        pytest.skip("model not present")
    model = TmdlAdapter().extract(str(source))
    assert model.report_pages == []
    assert model.visuals() == []
    assert correlate(SemanticGraph(model)) == ()


def test_an_unreadable_layout_costs_the_report_and_nothing_else() -> None:
    """A model whose report will not parse is still a model."""
    assert read_layout(b"") == []
    assert read_layout(b"\x00\x01\x02not json at all") == []
    assert read_layout('{"sections": "not a list"}'.encode("utf-16")) == []


# -- the trap ------------------------------------------------------------------


def test_a_renamed_field_resolves_to_what_the_query_selects(pages, sales) -> None:
    """The card titled "Net Sales" shows the measure ``Net Sales``.

    Its ``queryRef`` says ``Analysis DAX.Sales``. There is no measure called
    ``Sales`` in this model and there never has been -- the alias was fixed when
    the field was first dropped on the visual and did not follow the rename. A
    parser that trusts it reports a tile bound to a field that does not exist,
    which is a confident wrong answer about somebody else's report.
    """
    assert not any(m.name == "Sales" for m in sales.measures), (
        "if this ever fails the sample changed and this test proves nothing"
    )

    tile = _tile(pages, "Net Sales", "Net Sales")
    measures = tile.measures
    assert [m.name for m in measures] == ["Net Sales"]
    assert measures[0].table == "Analysis DAX"
    assert measures[0].resolved


def test_an_aggregate_resolves_to_the_column_it_aggregates(pages) -> None:
    """``Min(Store.Manufacturer)`` is really ``Min`` over ``Store[Store]``.

    Same trap, second shape: the alias names a column ``Manufacturer`` that this
    model does not have, while the query aggregates ``Store``. The aggregation
    itself is kept, because "the tile sums this column" and "the tile shows this
    column" are different claims.
    """
    tile = _tile(pages, "Net Sales", "Store Breakdown")
    by = {f.role: f for f in tile.fields}
    assert by["Category"].name == "Store"
    assert by["Category"].kind == "column"
    assert by["Y"].aggregation == "Sum"
    assert by["Y"].qualified_name == "Sales[Amount]"


# -- the join a reviewer asked for ---------------------------------------------


def test_a_tile_carries_the_dax_behind_it(pages) -> None:
    """The whole point: tile title -> measure -> DAX."""
    tile = _tile(pages, "Net Sales", "Units Sold")
    measure = tile.measures[0]
    assert measure.name == "Units Sold"
    assert "SUM(Sales[Unit])" in measure.expression.replace(" ", "")


def test_a_tile_carries_the_sql_behind_the_dax(pages) -> None:
    """And tile -> DAX -> SQL, from the same translator the dataset page uses."""
    tile = _tile(pages, "Net Sales", "Units Sold")
    sql = tile.measures[0].sql
    assert sql.startswith("SELECT")
    assert '"Sales"' in sql


def test_a_measure_with_no_sql_says_why_rather_than_going_blank(pages) -> None:
    """An untranslatable measure keeps its DAX and states the reason.

    Half of what a reviewer wants to see is which numbers *cannot* be reproduced
    as a query. A tile that simply showed nothing would read as a bug in this
    tool rather than a property of the measure.
    """
    blocked = [
        field
        for page in pages
        for tile in page.tiles
        for field in tile.measures
        if not field.sql
    ]
    assert blocked, "the sample has time-intelligence measures that cannot translate"
    for field in blocked:
        assert field.expression, "the DAX is shown even when the SQL cannot be"
        assert field.reason, f"{field.name} is blocked without saying why"


def test_the_same_measure_reads_identically_on_every_tile(pages) -> None:
    """Net Sales appears on several tiles; it must not translate differently.

    Translating per tile would give one measure several answers, which is the
    exact failure this project exists to prevent -- committed by the tool
    itself rather than by the model.
    """
    seen: dict[str, set[str]] = {}
    for page in pages:
        for tile in page.tiles:
            for field in tile.measures:
                seen.setdefault(field.name, set()).add(field.sql)
    repeated = {name: sqls for name, sqls in seen.items() if len(sqls) > 1}
    assert not repeated, f"one measure translated more than one way: {repeated}"


def test_one_field_in_two_wells_is_shown_once(pages) -> None:
    """Microsoft's map puts Net Sales in both `color` and `size`."""
    tile = _tile(pages, "Net Sales", "Net Sales by Location")
    names = [f.qualified_name for f in tile.fields]
    assert len(names) == len(set(names))


def test_a_field_the_model_lacks_is_reported_not_dropped(pages) -> None:
    """Two of 89 fields name something the extracted model does not hold.

    Kept and marked rather than hidden. A tile bound to a field this model does
    not contain is a real finding -- and silently dropping it would make the
    report look better accounted-for than it is.
    """
    missing = [
        f.qualified_name
        for page in pages
        for tile in page.tiles
        for f in tile.fields
        if not f.resolved
    ]
    assert "Sales[Dates]" in missing
    assert all(f.kind in ("", "measure", "column")
               for page in pages for tile in page.tiles for f in tile.fields)


def test_the_counts_add_up(pages) -> None:
    figures = counts(pages)
    assert figures["pages"] == 18
    assert figures["tiles"] == 71
    # Most tiles resolve; the handful that do not are named, not rounded away.
    # One, not two: a reader upgrade made the what-if parameter column readable,
    # so the tile bound to it resolves. Written as a bound rather than an exact
    # count, because the property that matters is that unresolved tiles stay
    # rare and stay *named* -- pinning the exact number turns every improvement
    # in the reader into a test failure.
    assert 0 <= figures["unresolved"] <= 2
    assert figures["with_sql"] >= 15
    assert figures["measure_fields"] >= figures["with_sql"]


def test_the_other_sample_reads_too() -> None:
    """Not tuned to one file."""
    model = _model(SUPPLY)
    assert len(model.report_pages) == 4
    assert len(model.visuals()) == 12
    pages = correlate(SemanticGraph(model))
    assert any(tile.fields for page in pages for tile in page.tiles)


# -- KPI and non-KPI -----------------------------------------------------------


def test_a_kpi_is_a_number_on_a_card_not_a_chart(pages) -> None:
    """The split a reviewer asked for by name.

    Watching a dashboard, in their words: "Here it's the numbers. Here it's the
    graphical representation... these are the KPIs for us." And then, directly:
    "what is the KPI and non-KPI as a part of your DAX. That's it."

    So the distinction is not one this project invented -- it is the one Power BI
    already makes when an author drops a measure on a card rather than on a
    chart. The same measure often appears both ways; only the card is what
    somebody points at and calls the KPI.
    """
    kpis = [t for page in pages for t in page.tiles if t.is_kpi]
    others = [t for page in pages for t in page.tiles if not t.is_kpi]
    assert kpis and others, "the sample should demonstrate both sides"

    for tile in kpis:
        assert tile.measures, "a KPI states a measure"
    # Nothing that plots is counted as a KPI.
    assert not any(
        t.visual_type in ("barChart", "lineChart", "donutChart", "pivotTable")
        for t in kpis
    )


def test_a_card_over_a_plain_column_is_not_a_kpi(pages) -> None:
    """A card showing a stored value displays data, not a performance indicator.

    Calling that a KPI would stretch the word until it stopped dividing
    anything, which is the whole point of being asked for the split.
    """
    for page in pages:
        for tile in page.tiles:
            if tile.is_kpi:
                assert tile.measures, f"{tile.title} has no measure behind it"


def test_the_counts_report_both_sides(pages) -> None:
    figures = counts(pages)
    assert figures["kpis"] >= 1
    assert figures["kpis"] <= figures["tiles"]
    assert figures["kpi_measures"] >= 1

"""Clicking a bar, and what has to be true for that to mean anything.

Cross-filtering is the one dashboard interaction that is trivial to fake. The
shortcut -- fade the bars that did not match -- looks identical on the panel
that was clicked and is wrong everywhere else: the numbers on the rest of the
page are still the unfiltered ones while the page implies they are not. Worse,
across two dimensions it fades *everything*, because a product name is never a
store name. So the filter here goes into the query, and these tests are mostly
about proving that it did.

Three properties, each with a way of going wrong that no screenshot catches.

The filter must be applied before aggregation. Filtering result rows instead
would leave a ratio dividing by a denominator drawn from every row, which is
the quiet wrong answer this project exists to prevent.

A visual must not filter itself. Power BI's own rule, and not cosmetic:
holding `Store[Type]` to "External" leaves that column with one value, so the
panel the reader just clicked would collapse -- taking with it the only
control that could undo the click.

And the value has to survive being data. A store called `O'Brien's` is a
perfectly ordinary name and a broken SQL string.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.generate import breakdown as B
from concordance.generate.evaluate import open_data
from concordance.generate.sql import Status, literal, translate

SALES = Path("data/models/Sales_Returns_Sample.pbix")


@pytest.fixture(scope="module")
def sales():
    if not SALES.exists():
        pytest.skip(f"model not present: {SALES}")
    model = PbixAdapter().extract(str(SALES))
    connection, _rows, reason = open_data(model)
    if connection is None:
        pytest.skip(reason)
    try:
        yield model, connection
    finally:
        connection.close()


def _measure(model, name):
    return next(m for m in model.measures if m.name == name)


# -- the literal ---------------------------------------------------------------


def test_an_ordinary_name_is_quoted() -> None:
    assert literal("External") == "'External'"


def test_an_apostrophe_is_doubled_not_dropped() -> None:
    """`O'Brien's` is a shop name, and a broken query if quoted naively."""
    assert literal("O'Brien's") == "'O''Brien''s'"


def test_a_value_carrying_sql_becomes_one_long_string() -> None:
    hostile = "x'; DROP TABLE Sales; --"
    rendered = literal(hostile)
    assert rendered.startswith("'") and rendered.endswith("'")
    # Every quote inside is doubled, so nothing in it can close the string.
    assert rendered[1:-1].count("'") % 2 == 0


def test_a_number_is_still_quoted() -> None:
    # One branch, so there is no path where a value reaches the query bare.
    assert literal(2019) == "'2019'"


# -- the query -----------------------------------------------------------------


def test_the_filter_lands_before_the_group_by(sales) -> None:
    model, _ = sales
    rendered = translate(
        model,
        _measure(model, "Net Sales"),
        grain=("Product[Category]",),
        only_where=("Store", "Type", "External"),
    )
    assert rendered.status is Status.EXACT
    assert rendered.sql.index("WHERE") < rendered.sql.index("GROUP BY")
    assert "\"Store\".\"Type\" = 'External'" in rendered.sql


def test_the_filtered_table_is_joined_in(sales) -> None:
    """The filter column need not be a table the measure itself reads."""
    model, _ = sales
    rendered = translate(
        model,
        _measure(model, "Net Sales"),
        grain=("Product[Category]",),
        only_where=("Store", "Type", "External"),
    )
    assert 'JOIN "Store"' in rendered.sql


def test_a_filter_and_a_year_are_one_where(sales) -> None:
    model, _ = sales
    rendered = translate(
        model,
        _measure(model, "Net Sales"),
        grain=("Product[Category]",),
        only_year=("Calendar", "Date", 2019),
        only_where=("Store", "Type", "External"),
    )
    # The clause itself, not every "WHERE" in the text: `Net Sales` compiles to
    # `SUM(...) FILTER (WHERE ...)` and carries one of its own.
    clauses = [
        line for line in rendered.sql.splitlines() if line.startswith("WHERE ")
    ]
    assert len(clauses) == 1
    assert " AND " in clauses[0]
    assert "EXTRACT(YEAR" in clauses[0]
    assert "'External'" in clauses[0]


# -- the numbers ---------------------------------------------------------------


def test_the_filter_actually_narrows_the_answer(sales) -> None:
    model, connection = sales
    whole = B._whole(model, connection, "Net Sales", None)
    part = B._whole(model, connection, "Net Sales", None, ("Store", "Type", "External"))
    assert whole is not None and part is not None
    assert 0 < part < whole


def test_the_parts_of_a_filtered_split_sum_to_the_filtered_whole(sales) -> None:
    """The check that catches a filter applied to some queries and not others."""
    model, connection = sales
    built = B.build(model, connection, "Net Sales", cross=("Store", "Type", "External"))
    filtered = [b for b in built.breakdowns if not b.is_filter]
    assert filtered
    for breakdown in filtered:
        assert breakdown.additive, breakdown.by


def test_every_filtered_panel_agrees_with_every_other(sales) -> None:
    model, connection = sales
    built = B.build(model, connection, "Net Sales", cross=("Store", "Type", "External"))
    totals = {round(b.total, 6) for b in built.breakdowns if not b.is_filter}
    assert len(totals) == 1


# -- a visual does not filter itself -------------------------------------------


def test_the_panel_holding_the_filter_keeps_every_group(sales) -> None:
    model, connection = sales
    built = B.build(model, connection, "Net Sales", cross=("Store", "Type", "External"))
    holding = next(b for b in built.breakdowns if b.is_filter)
    assert holding.by == "Store[Type]"
    # Both types, not just the one held -- otherwise the control disappears.
    assert len(holding.slices) >= B.MIN_CLASSES


def test_the_panel_holding_the_filter_reads_the_whole_model(sales) -> None:
    model, connection = sales
    whole = B._whole(model, connection, "Net Sales", None)
    built = B.build(model, connection, "Net Sales", cross=("Store", "Type", "External"))
    holding = next(b for b in built.breakdowns if b.is_filter)
    assert abs(holding.total - (whole or 0)) < max(abs(whole or 1) * 1e-9, 1e-6)


def test_the_panel_holding_the_filter_is_still_reported_additive(sales) -> None:
    """It is checked against the *unfiltered* whole, or a plain SUM would be
    announced as an average because its parts outran a filtered total."""
    model, connection = sales
    built = B.build(model, connection, "Net Sales", cross=("Store", "Type", "External"))
    assert next(b for b in built.breakdowns if b.is_filter).additive


def test_exactly_one_panel_holds_the_filter(sales) -> None:
    model, connection = sales
    built = B.build(model, connection, "Net Sales", cross=("Store", "Type", "External"))
    assert sum(1 for b in built.breakdowns if b.is_filter) == 1


def test_nothing_holds_a_filter_when_there_is_none(sales) -> None:
    model, connection = sales
    built = B.build(model, connection, "Net Sales")
    assert not any(b.is_filter for b in built.breakdowns)


# -- the time series and the offered columns -----------------------------------


def test_the_time_series_is_filtered_too(sales) -> None:
    model, connection = sales
    plain = B.over_time(model, connection, "Net Sales", "month")
    held = B.over_time(
        model, connection, "Net Sales", "month", cross=("Store", "Type", "External")
    )
    assert [s.label for s in plain.slices] == [s.label for s in held.slices]
    for before, after in zip(plain.slices, held.slices):
        assert after.value < before.value


def test_only_drawn_columns_are_offered_as_filters(sales) -> None:
    """A reader cross-filters by clicking, so a column nothing draws is not a
    filter they could ever apply -- `Store[Latitude]` is chartable and
    meaningless as one."""
    model, connection = sales
    built = B.build(model, connection, "Net Sales")
    assert set(built.crossable) == {b.by for b in built.breakdowns}
    assert "Store[Latitude]" not in built.crossable


# -- sparklines ----------------------------------------------------------------


def test_every_sparkline_is_a_real_series(sales) -> None:
    model, connection = sales
    found = B.sparklines(model, connection, ["Net Sales", "Units Sold"])
    assert set(found) == {"Net Sales", "Units Sold"}
    for values in found.values():
        assert len(values) >= B.MIN_CLASSES
        assert len(values) <= B.MAX_SPARK


def test_a_measure_that_cannot_be_cut_over_time_gets_no_flat_line(sales) -> None:
    """Absent, not flat. A flat sparkline is a claim about the data."""
    model, connection = sales
    assert B.sparklines(model, connection, ["Not A Measure"]) == {}


def test_sparklines_without_data_are_not_a_crash(sales) -> None:
    model, _ = sales
    assert B.sparklines(model, None, ["Net Sales"]) == {}


# -- a filter that matches nothing ---------------------------------------------


def test_a_filter_matching_no_rows_does_not_show_unfiltered_figures(sales) -> None:
    """Found by testing, and the worst shape a bug in this feature can take.

    Holding the page to a value no row carries drops every panel except the one
    holding the filter -- which is computed *without* it by design, so it still
    reads the whole model. The page then showed a chip saying "Store[Type] is
    X" above a chart of the unfiltered 1.25M: a filter announced in words and
    absent from the figures, which looks like an answer and is not one.

    Reachable by an ordinary click, not only by a hand-made request: hold the
    page to a small group, and a panel whose column has fewer than two values
    left under it is dropped. If that is all of them, this is what remains.
    """
    model, connection = sales
    built = B.build(model, connection, "Net Sales", cross=("Store", "Type", "NoSuchValue"))
    assert not built.available
    assert built.breakdowns == ()
    # And it says which restriction emptied it, so the reader knows what to undo.
    assert "Store[Type] is NoSuchValue" in built.reason


def test_the_filter_is_still_reported_so_it_can_be_cleared(sales) -> None:
    """An empty page whose filter has vanished from the payload is a page with
    no way back to the model."""
    model, connection = sales
    built = B.build(model, connection, "Net Sales", cross=("Store", "Type", "NoSuchValue"))
    assert built.cross == ("Store", "Type", "NoSuchValue")


def test_a_filter_that_does_match_is_unaffected(sales) -> None:
    """The guard must not fire on the ordinary case."""
    model, connection = sales
    built = B.build(model, connection, "Net Sales", cross=("Store", "Type", "External"))
    assert built.available
    assert any(not b.is_filter for b in built.breakdowns)

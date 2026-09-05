"""Splitting one measure by a dimension -- the numbers behind a chart.

The plumbing here is easy and the *selection* is not, so most of these tests
are about which columns get charted rather than about whether a query runs.
Store Sales is the fixture because every way this can go wrong is present in
it: a nine-value column of Flickr URLs, another of raw JPEG bytes, a 1,415-value
column called `Segment` that sounds like a dimension, and the two best splits in
the file sitting on a table the project's other picker deliberately skips.

The load-bearing test is `test_a_split_adds_up_to_the_whole`: any grouping
query returns plausible-looking numbers, and what catches a wrong one is that
the parts have to sum to the figure the KPI card shows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.generate import breakdown as B
from concordance.generate.evaluate import evaluate, open_data
from concordance.generate.sql import Status, translate

STORE = Path("data/models/StoreSales.pbix")


@pytest.fixture(scope="module")
def store():
    if not STORE.exists():
        pytest.skip(f"model not present: {STORE}")
    model = PbixAdapter().extract(str(STORE))
    connection, _rows, reason = open_data(model)
    if connection is None:
        pytest.skip(reason)
    try:
        yield model, connection
    finally:
        connection.close()


def _named(candidates) -> set[str]:
    return {f"{t}[{c}]" for t, c, _ in candidates}


# -- are the right columns offered? -------------------------------------------


def test_the_two_cleanest_splits_in_the_file_are_offered(store) -> None:
    """`Store[Chain]` and `Store[Store type]` are two-way splits of the fact table.

    Neither is reachable through `_grain_options`, which offers only leaves --
    `Store` points at `District`, so that rule drops it. Charting is a different
    question from grouping: all that matters is that `Store` is on the "one"
    side of a relationship, so grouping by it cannot multiply rows.
    """
    offered = _named(B.chartable(*store))
    assert "Store[Chain]" in offered
    assert "Store[Store type]" in offered


def test_a_column_of_urls_is_rejected_even_though_its_name_is_innocent(store) -> None:
    """`District[DM_Pic_fl]` holds `http://farm6.staticflickr.com/...`.

    It has nine distinct values, so cardinality waves it through, and it does
    not end in `pic`, so a name rule waves it through too. Only looking at a
    value catches it.
    """
    assert "District[DM_Pic_fl]" not in _named(B.chartable(*store))


def test_a_column_of_image_bytes_is_rejected(store) -> None:
    assert "District[DMImage]" not in _named(B.chartable(*store))


def test_a_column_with_too_many_values_is_not_a_chart(store) -> None:
    """`Item[Segment]` holds 1,415 values. 1,415 bars is not a chart."""
    assert "Item[Segment]" not in _named(B.chartable(*store))


def test_join_keys_are_not_offered(store) -> None:
    model, _ = store
    offered = _named(B.chartable(*store))
    for relationship in model.relationships:
        assert f"{relationship.to_table}[{relationship.to_column}]" not in offered


def test_every_offered_column_is_within_the_class_bounds(store) -> None:
    for _table, _column, count in B.chartable(*store):
        assert B.MIN_CLASSES <= count <= B.MAX_CLASSES


def test_the_clearest_split_is_offered_first(store) -> None:
    counts = [count for _t, _c, count in B.chartable(*store)]
    assert counts == sorted(counts)


# -- naming rules --------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["ID", "CategoryID", "StoreKey", "PostalCode", "DM_Pic", "Category (clusters) 2"]
)
def test_identifier_names_are_rejected(name: str) -> None:
    assert B._named_like_an_id(name)


@pytest.mark.parametrize("name", ["Category", "Chain", "Store type", "Territory", "Codex"])
def test_a_dimension_name_survives_the_identifier_rule(name: str) -> None:
    """`Category` has to survive a rule aimed at `CategoryID`, and `Codex` one
    aimed at `code`: these are suffixes and whole names, never substrings."""
    assert not B._named_like_an_id(name)


@pytest.mark.parametrize(
    "sample",
    [b"\xff\xd8\xff", bytearray(b"x"), "http://x/y", "https://x/y", "www.x.com", "data:image/png;base64,AA", "x" * 61],
)
def test_unreadable_values_are_caught_by_looking(sample) -> None:
    assert B._unreadable(sample)


@pytest.mark.parametrize("sample", ["Fashions Direct", "020-Mens", "", None, 7, "New Store"])
def test_a_readable_label_is_not_rejected(sample) -> None:
    assert not B._unreadable(sample)


# -- are the numbers right? ----------------------------------------------------


def test_a_split_adds_up_to_the_whole(store) -> None:
    """The parts have to sum to the figure on the card above them.

    This is the test that catches a grouping query that runs and is wrong. A
    chart whose bars sum to something other than the total is either summing
    the wrong column or dropping rows in a join, and both look healthy.
    """
    model, connection = store
    whole = evaluate(model).by_name()["Sales"]
    assert whole.computed, whole.reason

    for table, column in [("Store", "Chain"), ("Item", "Category")]:
        split = B.one(model, connection, "Sales", table, column)
        assert split.drawable, split.reason
        assert split.total == pytest.approx(whole.value, rel=1e-9)


def test_a_two_way_split_has_exactly_two_slices(store) -> None:
    model, connection = store
    split = B.one(model, connection, "Sales", "Store", "Chain")
    assert {s.label for s in split.slices} == {"Fashions Direct", "Lindseys"}


def test_slices_come_back_largest_first(store) -> None:
    model, connection = store
    split = B.one(model, connection, "Sales", "Item", "Category")
    values = [s.value for s in split.slices]
    assert values == sorted(values, reverse=True)


def test_the_query_that_produced_the_numbers_comes_back_with_them(store) -> None:
    model, connection = store
    split = B.one(model, connection, "Sales", "Store", "Chain")
    assert "GROUP BY" in split.sql.upper()


def test_a_measure_that_does_not_translate_says_so_rather_than_charting(store) -> None:
    model, connection = store
    split = B.one(model, connection, "Sales", "Fiscal calendar", "FiscalYear")
    assert not split.drawable
    assert "relationship" in split.reason


def test_an_unknown_measure_is_named_in_the_reason(store) -> None:
    model, connection = store
    split = B.one(model, connection, "Nonexistent Measure", "Store", "Chain")
    assert not split.drawable
    assert "Nonexistent Measure" in split.reason


# -- the assembled dashboard ---------------------------------------------------


def test_a_dashboard_shows_four_different_angles_not_one_four_times(store) -> None:
    """One column per table.

    `District`, `DistrictName` and `DM` are three drawings of the same nine
    districts. Without the spread rule the row would show them all and say
    nothing three times over.
    """
    model, connection = store
    built = B.build(model, connection, "Sales")
    assert built.available
    tables = [b.table for b in built.breakdowns]
    assert len(tables) == len(set(tables))


def test_every_breakdown_on_a_dashboard_is_drawable(store) -> None:
    model, connection = store
    built = B.build(model, connection, "Sales")
    assert built.breakdowns
    assert all(b.drawable for b in built.breakdowns)


def test_the_full_candidate_list_travels_with_the_dashboard(store) -> None:
    """So the reader can chart by something other than the four picked."""
    model, connection = store
    built = B.build(model, connection, "Sales")
    assert len(built.dimensions) > len(built.breakdowns)
    assert {"table", "column", "value"} <= set(built.dimensions[0])


def test_no_data_is_reported_rather_than_guessed(store) -> None:
    model, _ = store
    built = B.build(model, None, "Sales")
    assert not built.available
    assert built.reason


def test_a_measure_that_is_not_in_the_model_is_named_in_the_refusal(store) -> None:
    """Never an empty grid the reader has to interpret."""
    model, connection = store
    built = B.build(model, connection, "Nonexistent Measure")
    assert not built.available
    assert "Nonexistent Measure" in built.reason


def test_an_untranslatable_measure_is_not_blamed_on_the_dimensions(store) -> None:
    """The reason has to point at the thing that is actually broken.

    A measure that cannot be compiled cannot be split by anything either, so
    reporting "nothing in this model splits this measure into readable groups"
    blames every dimension in the file for the measure's own blocker -- wrong,
    and the opposite of useful to whoever has to fix it.
    """
    model, connection = store
    blocked = next(
        (m.name for m in model.measures if translate(model, m).status is not Status.EXACT),
        None,
    )
    if blocked is None:
        pytest.skip("every measure in this fixture translates")
    built = B.build(model, connection, blocked)
    assert not built.available
    assert blocked in built.reason
    # The measure's own blocker, not a count of groups.
    assert str(B.MIN_CLASSES) not in built.reason


def test_a_long_tail_is_folded_rather_than_dropped(store) -> None:
    """A chart of the top ten that silently lost the eleventh would not add up."""
    model, connection = store
    split = B.one(model, connection, "Sales", "Item", "Category")
    assert len(split.slices) <= B.MAX_SLICES + 1
    if split.folded:
        assert split.slices[-1].label == f"{split.folded} more"


def test_folding_keeps_the_total_intact(store) -> None:
    """`Store[Territory]` has eleven groups, so one is folded. The folded slice
    carries its value rather than discarding it, which is the only reason the
    chart still sums to the card."""
    model, connection = store
    split = B.one(model, connection, "Sales", "Store", "Territory")
    assert split.folded == 1
    assert split.slices[-1].label == "1 more"

    whole = evaluate(model).by_name()["Sales"]
    assert split.total == pytest.approx(whole.value, rel=1e-9)


# -- does the measure add up? --------------------------------------------------


def test_an_additive_measure_is_marked_as_one(store) -> None:
    model, connection = store
    whole = evaluate(model).by_name()["Sales"].value
    split = B.one(model, connection, "Sales", "Store", "Chain", whole=whole)
    assert split.additive
    assert split.total == pytest.approx(whole)


def test_an_average_is_not_marked_as_adding_up(store) -> None:
    """`Average Selling Area Size` splits into a fair comparison whose parts
    are not a quantity of anything added together.

    Nothing in the measure's name says so, and the DAX says so only if you
    notice the outer aggregate is an `AVG`. Printing "totals 59,302" under that
    chart would state a figure the model does not contain -- the whole-model
    average is 24,327 -- so the two are run and compared instead.
    """
    model, connection = store
    whole = evaluate(model).by_name()["Average Selling Area Size"].value
    split = B.one(
        model, connection, "Average Selling Area Size", "Store", "Chain", whole=whole
    )
    assert split.drawable
    assert not split.additive
    assert split.total != pytest.approx(whole)


def test_a_dashboard_marks_additivity_without_being_told(store) -> None:
    model, connection = store
    assert all(b.additive for b in B.build(model, connection, "Sales").breakdowns)
    assert not any(
        b.additive
        for b in B.build(model, connection, "Average Selling Area Size").breakdowns
    )


def test_nothing_adds_up_to_a_whole_that_does_not_exist(store) -> None:
    """A measure with no single figure gets no additivity claim either."""
    model, connection = store
    split = B.one(model, connection, "Sales", "Store", "Chain")
    assert split.whole is None
    assert not split.additive


@pytest.mark.parametrize(
    ("parts", "whole", "expected"),
    [
        (100.0, 100.0, True),
        (100.0, 100.0000000001, True),  # float drift over a million rows
        (100.0, 101.0, False),
        (0.0, 0.0, True),
        (100.0, None, False),
        (-50.0, -50.0, True),
    ],
)
def test_the_tolerance_is_relative_not_absolute(parts, whole, expected) -> None:
    assert B._adds_up(parts, whole) is expected


# -- ordering and the year filter ----------------------------------------------


def test_a_real_period_carries_the_date_it_sits_at(store) -> None:
    """The anchor is what makes "in date order" real rather than a guess.

    Power BI records a column's display order in a sort-by column this file's
    reader does not expose, so year and month labels cannot be ordered by
    reading them. The table those labels come from also holds real dates, and
    the earliest date in each group is a fact in the data rather than an
    inference about what the words mean.
    """
    model, connection = store
    anchors = B._anchors(model, connection, "Fiscal calendar", "FiscalYear")
    assert len(anchors) == 3
    assert [y for y, _ in sorted(anchors.items(), key=lambda kv: kv[1])] == [
        "2012",
        "2013",
        "2014",
    ]


def test_a_column_that_merely_owns_a_date_is_not_a_period(store) -> None:
    """`Store` holds an opening date, so every chain has an earliest one.

    That number is real and ordering by it would still be a lie: both chains
    have been opening stores across the same decade, so "Lindseys, then
    Fashions Direct" is an ordering of nothing presented as a chronology. What
    separates the two cases is whether the groups' date spans overlap, which is
    measured rather than guessed from the column's name.
    """
    model, connection = store
    assert B._date_columns(model, "Store") == ["Opening date"]
    assert B._anchors(model, connection, "Store", "Chain") == {}


def test_a_month_that_repeats_across_years_is_not_one_point_in_time(store) -> None:
    """Store Sales' fiscal calendar covers three years, so its "Jan" is January
    2013 *and* January 2014.

    Ordering those twelve labels would imply a chronology the data does not
    have. This is the case a name-based rule gets wrong in the confident
    direction -- `FiscalMonth` could hardly sound more like a period.
    """
    model, connection = store
    assert B._anchors(model, connection, "Fiscal calendar", "FiscalMonth") == {}


def test_a_split_that_is_not_a_period_carries_no_order(store) -> None:
    model, connection = store
    split = B.one(model, connection, "Sales", "Store", "Chain")
    assert all(s.order == "" for s in split.slices)


def test_store_sales_refuses_a_year_filter_it_cannot_honour(store) -> None:
    """`Fiscal calendar` is a proper leaf holding real dates, and no active
    relationship joins it to `Sales`.

    So the years are *present* in the model and still not usable: filtering on
    them would silently drop every row. Offering the control and having it
    return nothing is worse than not offering it, so the option list is empty
    while the raw year list is not.
    """
    model, connection = store
    assert B.available_years(model, connection)
    assert B.usable_years(model, connection, "Sales") == []
    assert B.build(model, connection, "Sales").years == ()


def test_a_year_that_was_never_offered_is_not_applied(store) -> None:
    model, connection = store
    built = B.build(model, connection, "Sales", year=2013)
    assert built.year is None
    assert "EXTRACT(YEAR" not in built.breakdowns[0].sql


def test_the_leaf_rule_keeps_the_calendar_and_drops_the_fact_table(store) -> None:
    """Being pointed at is not enough to be a date dimension.

    In Sales & Returns, `Customer` points at `Sales`, which makes `Sales`
    something-pointed-at while it is plainly the fact table -- and it carries a
    `Date` column of its own. Only the leaf test separates them.
    """
    model, _ = store
    referenced = {r.to_table for r in model.relationships}
    references = {r.from_table for r in model.relationships}
    for table in referenced & references:
        assert B.calendar_column(model) != (table, "Date")


@pytest.fixture(scope="module")
def sales_returns():
    """Microsoft's Sales & Returns sample -- the one model here with a calendar
    a year filter can safely stand on."""
    path = Path("data/models/Sales_Returns_Sample.pbix")
    if not path.exists():
        pytest.skip(f"model not present: {path}")
    model = PbixAdapter().extract(str(path))
    connection, _rows, reason = open_data(model)
    if connection is None:
        pytest.skip(reason)
    try:
        yield model, connection
    finally:
        connection.close()


def test_a_year_can_be_applied_where_the_calendar_is_reachable(sales_returns) -> None:
    model, connection = sales_returns
    built = B.build(model, connection, "Net Sales", year=2019)
    assert built.years == (2019,)
    assert built.year == 2019
    assert all("EXTRACT(YEAR" in b.sql for b in built.breakdowns)


def test_a_filtered_chart_is_still_known_to_add_up(sales_returns) -> None:
    """The regression that shipped for one build and was caught on screen.

    Filtering to a year while checking the parts against *every* year's total
    finds them unequal and concludes the measure is non-additive -- so every
    chart announced `Net Sales` as "an average or a ratio", which is wrong and
    confidently worded. The additivity test is only a test of additivity when
    both sides read the same rows.
    """
    model, connection = sales_returns
    built = B.build(model, connection, "Net Sales", year=2019)
    assert built.available
    assert all(b.additive for b in built.breakdowns), [
        (b.by, b.total, b.whole) for b in built.breakdowns
    ]
    for breakdown in built.breakdowns:
        assert breakdown.total == pytest.approx(breakdown.whole, rel=1e-9)


def test_months_of_a_single_year_are_orderable(sales_returns) -> None:
    """The chart from the screenshot that started this.

    Every month of 2019 occupies its own stretch of the calendar, so the groups
    partition time and can be put in real date order -- which alphabetical
    never would, since that leads with April.
    """
    model, connection = sales_returns
    split = B.one(model, connection, "Net Sales", "Calendar", "Month")
    assert all(s.order for s in split.slices)
    in_time = [s.label for s in sorted(split.slices, key=lambda s: s.order)]
    assert in_time == ["Jan", "Feb", "Mar", "Apr", "May", "Jun"]
    assert in_time != sorted(in_time)

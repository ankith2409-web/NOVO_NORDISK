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


def test_a_measure_with_nothing_to_split_by_says_why(store) -> None:
    """An untranslatable measure yields no charts and an explanation, never an
    empty grid the reader has to interpret."""
    model, connection = store
    built = B.build(model, connection, "Nonexistent Measure")
    assert not built.available
    assert str(B.MIN_CLASSES) in built.reason


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

"""Cutting a measure over time.

Every other split in this project groups by a column. This one groups by an
*expression* -- `DATE_TRUNC('month', Calendar[Date])` -- which is a different
thing and fails differently. Three things have to hold, and each has a test
that goes red without it.

The bucket has to be cut in the query rather than off the returned labels: a
chart grouped by the word "January" puts January 2019 and January 2020 in one
bar and says nothing about having done so.

A period is offered only where the data really falls into that many buckets of
it. Store Sales is the useful negative here and Sales & Returns the useful
positive: the latter holds six months of 2019, so "by year" is one bucket and
is correctly *not* offered while "by month" is.

And the series is never folded. Every other breakdown keeps ten slices and sums
the tail, which for a time series would print "26 more" and destroy the only
thing the chart is for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.generate import breakdown as B
from concordance.generate.evaluate import open_data
from concordance.generate.sql import PERIODS, Status, translate

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


# -- which periods are offered -------------------------------------------------


def test_month_is_offered_for_a_file_holding_six_months(sales) -> None:
    assert "month" in B.usable_periods(*sales, "Net Sales")


def test_year_is_not_offered_for_a_file_holding_one_year(sales) -> None:
    """One bucket is not a chart; it is the figure the card already showed."""
    assert "year" not in B.usable_periods(*sales, "Net Sales")


def test_a_measure_that_cannot_reach_the_calendar_is_offered_nothing(sales) -> None:
    model, connection = sales
    assert B.usable_periods(model, connection, "Not A Measure") == []


# -- the translation -----------------------------------------------------------


def test_the_bucket_is_cut_in_sql_not_off_the_label(sales) -> None:
    """Grouping by a rendered month name merges the same month across years."""
    model, _ = sales
    rendered = translate(
        model, _measure(model, "Net Sales"), period=("month", "Calendar", "Date")
    )
    assert rendered.status is Status.EXACT
    assert "DATE_TRUNC('month'" in rendered.sql
    assert rendered.sql.count("DATE_TRUNC('month'") >= 2  # SELECT and GROUP BY


def test_an_unknown_period_is_refused_by_name(sales) -> None:
    """The period name is interpolated into `DATE_TRUNC`, so it is checked."""
    model, _ = sales
    rendered = translate(
        model,
        _measure(model, "Net Sales"),
        period=("month'); DROP TABLE Sales; --", "Calendar", "Date"),
    )
    assert rendered.status is not Status.EXACT
    assert "DROP TABLE" not in rendered.sql


def test_every_offered_period_actually_translates(sales) -> None:
    model, connection = sales
    for period in B.usable_periods(model, connection, "Net Sales"):
        assert period in PERIODS
        rendered = translate(
            model, _measure(model, "Net Sales"), period=(period, "Calendar", "Date")
        )
        assert rendered.status is Status.EXACT, period


def test_a_period_can_be_combined_with_a_year_filter(sales) -> None:
    model, _ = sales
    rendered = translate(
        model,
        _measure(model, "Net Sales"),
        only_year=("Calendar", "Date", 2019),
        period=("month", "Calendar", "Date"),
    )
    assert rendered.status is Status.EXACT
    assert "EXTRACT(YEAR" in rendered.sql
    assert "DATE_TRUNC('month'" in rendered.sql


# -- the series ----------------------------------------------------------------


def test_net_sales_by_month_is_the_six_months_the_file_holds(sales) -> None:
    cut = B.over_time(*sales, "Net Sales", "month")
    assert cut.drawable, cut.reason
    assert [s.label for s in cut.slices] == [
        "Jan 2019", "Feb 2019", "Mar 2019", "Apr 2019", "May 2019", "Jun 2019",
    ]


def test_june_matches_what_power_bi_shows_for_the_same_file(sales) -> None:
    """The report carries a report-level filter pinning it to June, and shows
    387.1K. This cut, filtered by nothing, puts the same figure in June -- which
    is the strongest available check that the bucketing is right."""
    cut = B.over_time(*sales, "Net Sales", "month")
    june = next(s for s in cut.slices if s.label == "Jun 2019")
    assert round(june.value / 1000, 1) == 387.1


def test_the_series_is_in_date_order_not_by_size(sales) -> None:
    cut = B.over_time(*sales, "Net Sales", "month")
    assert [s.order for s in cut.slices] == sorted(s.order for s in cut.slices)
    # And it is genuinely not size order, or the test above proves nothing.
    assert [s.value for s in cut.slices] != sorted(
        (s.value for s in cut.slices), reverse=True
    )


def test_every_point_carries_a_real_time_anchor(sales) -> None:
    for slice_ in B.over_time(*sales, "Net Sales", "month").slices:
        assert slice_.order.startswith("2019-")


def test_the_series_is_never_folded(sales) -> None:
    """`folded` stays zero and no slice is named "N more"."""
    cut = B.over_time(*sales, "Net Sales", "day")
    assert cut.folded == 0
    assert len(cut.slices) > B.MAX_SLICES
    assert not any(s.label.endswith("more") for s in cut.slices)


def test_a_period_is_a_grain_so_the_months_sum_to_the_whole(sales) -> None:
    """The cut is a grouping, not a filter, so nothing may be lost by it."""
    model, connection = sales
    whole = B._whole(model, connection, "Net Sales", None)
    parts = sum(s.value for s in B.over_time(model, connection, "Net Sales", "month").slices)
    assert whole is not None
    assert abs(parts - whole) < max(abs(whole) * 1e-9, 1e-6)


def test_an_unoffered_period_is_not_charted_by_the_dashboard(sales) -> None:
    built = B.build(*sales, "Net Sales", period="year")
    assert "year" not in built.periods
    assert built.over_time is None


def test_the_dashboard_carries_the_series_when_a_period_is_asked_for(sales) -> None:
    built = B.build(*sales, "Net Sales", period="month")
    assert built.over_time is not None
    assert built.over_time.by == "by month"


def test_a_summed_measure_over_time_is_reported_as_additive(sales) -> None:
    """The panel prints a sentence under the chart, and it has to be earned.

    The first version of `over_time` had no whole to check against and said
    `additive=False`, so the interface told a reader that Net Sales -- a plain
    filtered SUM -- "is an average or a ratio". Caught on screen, not by a
    test, which is why this one exists.
    """
    model, connection = sales
    whole = B._whole(model, connection, "Net Sales", None)
    cut = B.over_time(model, connection, "Net Sales", "month", whole=whole)
    assert cut.additive
    assert cut.whole == whole


def test_a_ratio_over_time_is_still_reported_as_non_additive(sales) -> None:
    """The check is a measurement, so it has to fail where it should."""
    model, connection = sales
    whole = B._whole(model, connection, "Return Rate", None)
    cut = B.over_time(model, connection, "Return Rate", "month", whole=whole)
    if cut.drawable and whole is not None:
        assert not cut.additive


def test_the_dashboard_hands_the_series_its_whole(sales) -> None:
    built = B.build(*sales, "Net Sales", period="month")
    assert built.over_time is not None
    assert built.over_time.additive

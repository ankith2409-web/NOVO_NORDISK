"""Arithmetic a report does in its visuals rather than in a measure.

Microsoft's AdventureWorks sample is the case this exists for: it carries
exactly one measure, which uses `USERELATIONSHIP` and cannot be compiled, while
every figure a reader sees on its three pages is an implicit `Sum` declared on
a tile. Before this the dashboard had nothing to show for a file with a hundred
million dollars of sales in it -- the arithmetic was right there, just not in
`model.measures`, which was the only place anything looked.

Two properties matter more than the feature does.

**These must never become model objects.** Merging them into `model.measures`
would inflate the measure count, put invented objects into the BRD and the
FRD, and change the drift fingerprint of a file nobody edited -- a fabrication
of exactly the kind this project exists to catch.

**Nothing may be guessed.** Every one is a table, a column and an aggregation
the tile itself states, rewritten into the DAX that says the same thing. The
tests below pin the cases where a naive reading would invent something:
`Min of Store` over a text column, `Sum of ProductID` over a key, and two
tables that both carry an `Amount`.

Every fixture skips when its `.pbix` is absent, which is this suite's existing
convention -- the sample binaries are not all in version control.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.generate import implicit
from concordance.generate.evaluate import evaluate, open_data
from concordance.generate.sql import Status, translate

ADVENTURE = Path("data/models/AdventureWorks_Sales.pbix")
SALES = Path("data/models/Sales_Returns_Sample.pbix")


def _model(path: Path):
    if not path.exists():
        pytest.skip(f"model not present: {path}")
    return PbixAdapter().extract(str(path))


@pytest.fixture(scope="module")
def adventure():
    model = _model(ADVENTURE)
    connection, _rows, reason = open_data(model)
    if connection is None:
        pytest.skip(reason)
    try:
        yield model, connection
    finally:
        connection.close()


# -- what is read --------------------------------------------------------------


def test_the_reports_own_sums_are_found(adventure) -> None:
    model, _ = adventure
    names = {m.name for m in implicit.from_report(model)}
    assert names == {"Sum of Sales Amount", "Sum of Order Quantity"}


def test_each_one_is_the_dax_the_tile_states(adventure) -> None:
    model, _ = adventure
    found = next(
        m for m in implicit.from_report(model) if m.name == "Sum of Sales Amount"
    )
    assert found.expression == "SUM(Sales[Sales Amount])"
    assert found.table == "Sales"
    assert found.depends_on_columns == frozenset({("Sales", "Sales Amount")})


def test_the_same_aggregation_on_several_tiles_is_one_calculation(adventure) -> None:
    """AdventureWorks drops `Sum of Sales Amount` on three separate visuals."""
    model, _ = adventure
    names = [m.name for m in implicit.from_report(model)]
    assert len(names) == len(set(names))


# -- what is deliberately not read ---------------------------------------------


def test_a_min_over_a_text_column_is_not_offered() -> None:
    """Power BI's default aggregation for a text column on a tile is `Min`,
    which is how it says "just show the label". Taken at face value it produces
    "Min of Store" -- an alphabetically-first string offered as a metric."""
    model = _model(SALES)
    for measure in implicit.from_report(model):
        assert not measure.name.startswith("Min of ")
        assert not measure.name.startswith("Max of ")


def test_a_key_is_not_summed() -> None:
    """`Sum of ProductID` is a number with no referent."""
    model = _model(SALES)
    assert not any(
        m.name.casefold().endswith("id") for m in implicit.from_report(model)
    )


def test_two_tables_with_the_same_column_get_distinguishable_names() -> None:
    """Sales & Returns has an `Amount` on two tables. "Sum of Amount" twice in
    one list names two different calculations identically, which is the exact
    ambiguity this project removes."""
    model = _model(SALES)
    names = [m.name for m in implicit.from_report(model)]
    assert len(names) == len(set(names))
    amounts = [n for n in names if "Amount" in n]
    if len(amounts) > 1:
        assert all("[" in n for n in amounts)


def test_a_field_naming_a_column_the_model_lacks_is_skipped(adventure) -> None:
    """Reported as a coverage gap already; querying it would turn a known gap
    into a wrong number."""
    model, _ = adventure
    columns = {(c.table, c.name) for c in model.columns}
    for measure in implicit.from_report(model):
        for pair in measure.depends_on_columns:
            assert pair in columns


def test_an_authored_measure_wins_over_a_derived_one() -> None:
    model = _model(SALES)
    authored = {m.name.casefold() for m in model.measures}
    for measure in implicit.from_report(model):
        assert measure.name.casefold() not in authored


# -- they are not model objects ------------------------------------------------


def test_nothing_is_added_to_the_model(adventure) -> None:
    model, _ = adventure
    before = len(model.measures)
    implicit.from_report(model)
    implicit.all_measures(model)
    assert len(model.measures) == before == 1


def test_each_carries_a_fingerprint_that_cannot_pass_for_an_authored_one(
    adventure,
) -> None:
    """A fingerprint is how this project decides two things are the same across
    two versions of a file. A collision would let an invented object be
    accepted in place of an authored one."""
    model, _ = adventure
    authored = {m.fingerprint for m in model.measures}
    for measure in implicit.from_report(model):
        assert implicit.is_implicit(measure)
        assert measure.fingerprint not in authored


def test_the_fingerprint_is_stable_across_reads(adventure) -> None:
    model, _ = adventure
    first = {m.name: m.fingerprint for m in implicit.from_report(model)}
    second = {m.name: m.fingerprint for m in implicit.from_report(model)}
    assert first == second


def test_an_authored_measure_is_not_reported_as_implicit(adventure) -> None:
    model, _ = adventure
    for measure in model.measures:
        assert not implicit.is_implicit(measure)


# -- they actually compute -----------------------------------------------------


def test_each_one_translates_to_sql(adventure) -> None:
    model, _ = adventure
    for measure in implicit.from_report(model):
        rendered = translate(model, measure)
        assert rendered.status is Status.EXACT, measure.name


def test_the_figures_are_the_files_own(adventure) -> None:
    model, connection = adventure
    run = evaluate(model, connection=connection, extra=implicit.from_report(model))
    by_name = {v.measure: v for v in run.values}
    assert round(by_name["Sum of Sales Amount"].value or 0) == 109_809_274
    assert round(by_name["Sum of Order Quantity"].value or 0) == 274_776


def test_a_derived_figure_is_flagged_and_an_authored_one_is_not(adventure) -> None:
    model, connection = adventure
    run = evaluate(model, connection=connection, extra=implicit.from_report(model))
    by_name = {v.measure: v for v in run.values}
    assert by_name["Sum of Sales Amount"].implicit
    assert not by_name["Sales Amount by Due Date"].implicit


def test_the_models_own_untranslatable_measure_is_still_reported(adventure) -> None:
    """Adding these must not hide what the file cannot do."""
    model, connection = adventure
    run = evaluate(model, connection=connection, extra=implicit.from_report(model))
    blocked = next(v for v in run.values if v.measure == "Sales Amount by Due Date")
    assert blocked.value is None
    assert "USERELATIONSHIP" in blocked.reason


# -- and they carry the whole dashboard ----------------------------------------


def test_a_model_whose_only_measure_is_blocked_still_gets_a_dashboard(
    adventure,
) -> None:
    from concordance.generate import breakdown as B

    model, connection = adventure
    built = B.build(model, connection, "Sum of Sales Amount", period="month")
    assert built.available
    assert len(built.breakdowns) >= 3
    # Every panel agrees, which is what says the grain is right.
    assert len({round(b.total, 3) for b in built.breakdowns}) == 1
    assert all(b.additive for b in built.breakdowns)


def test_an_untranslatable_measure_is_not_blamed_on_the_dimensions(adventure) -> None:
    """The reason has to point at the thing that is actually broken.

    The page used to report "nothing in this model splits this measure into
    readable groups", which blamed all eleven of AdventureWorks' tables for a
    blocker belonging to its one measure -- wrong, and the opposite of useful
    to whoever has to fix it.
    """
    from concordance.generate import breakdown as B

    model, connection = adventure
    built = B.build(model, connection, "Sales Amount by Due Date")
    assert not built.available
    assert "USERELATIONSHIP" in built.reason
    assert str(B.MIN_CLASSES) not in built.reason


def test_the_calendar_is_found_despite_three_date_columns_on_it(adventure) -> None:
    """`Date` carries `Date`, `Month` and `Full Date`. The old rule wanted
    exactly one date column anywhere and so declined a textbook date
    dimension; the finest grain wins instead, measured rather than guessed."""
    from concordance.generate import breakdown as B

    model, connection = adventure
    found = B.calendar_column(model, connection)
    assert found is not None
    table, column = found
    assert table == "Date"
    # The day-level key, not the month it rolls up to.
    fine = connection.execute(
        f'SELECT COUNT(DISTINCT "{column}") FROM "{table}"'
    ).fetchone()
    coarse = connection.execute('SELECT COUNT(DISTINCT "Month") FROM "Date"').fetchone()
    assert fine[0] > coarse[0]


def test_without_a_connection_the_ambiguity_is_still_refused(adventure) -> None:
    """Nothing to measure means nothing to choose between, so the old refusal
    stands rather than a guess being made."""
    from concordance.generate import breakdown as B

    model, _ = adventure
    assert B.calendar_column(model) is None

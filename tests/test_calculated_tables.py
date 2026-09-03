"""A table whose rows are produced by DAX, and the columns it names.

The gap this closes was found by looking at the interface rather than at the
code. Microsoft's Store Sales sample -- the file this project opens on -- showed
eight "unresolved reference" warnings on its front page, every one of them
naming a column of a `Date` table. The model has that table. Its own field
descriptions tell report authors to use it ("Legacy fiscal calendar. Do not use
for new visuals -- use the Date table (with Fiscal Hierarchy) instead"). Both of
its drill-down hierarchies are defined against it.

It was invisible because it is a *calculated* table: its rows come from
`ADDCOLUMNS(CALENDAR(...), ...)` evaluated at refresh, so it stores no data to
enumerate and appears in neither the stored-table list nor the stored-column
list. A reader that only enumerates what is stored therefore loses the whole
table, and then complains about the model for referring to it -- eight confident
wrong answers about somebody else's work, on the first screen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.graph.csg import SemanticGraph
from concordance.normalize.calctable import added_columns, calendar_column, column_names

STORE = Path("data/models/StoreSales.pbix")
SALES = Path("data/models/Sales_Returns_Sample.pbix")


@pytest.fixture(scope="module")
def store():
    if not STORE.exists():
        pytest.skip(f"model not present: {STORE}")
    return PbixAdapter().extract(str(STORE))


# -- reading the expression ----------------------------------------------------


def test_addcolumns_names_are_read_with_the_dax_behind_each() -> None:
    pairs = added_columns('ADDCOLUMNS(Sales, "Margin", [Revenue] - [Cost], "Flag", 1)')
    assert pairs == [("Margin", "[Revenue]-[Cost]"), ("Flag", "1")]


def test_a_comma_inside_a_nested_call_does_not_split_an_argument() -> None:
    """The reason this uses the tokenizer rather than `str.split(",")`.

    `FORMAT([Date], "mmm")` is Store Sales' own, and it is the shape that breaks
    a naive split twice over: the body is truncated to `FORMAT([Date]`, and the
    format string left behind is then read as the *name* of the column after
    it. One careless comma turns a correct column into two wrong ones.
    """
    pairs = added_columns(
        'ADDCOLUMNS(CALENDAR(DATE(2012,1,1), DATE(2015,12,31)),'
        ' "Month", FORMAT([Date], "mmm"), "Year", YEAR([Date]))'
    )
    assert pairs == [("Month", 'FORMAT([Date],"mmm")'), ("Year", "YEAR([Date])")]


def test_an_expression_beginning_with_a_literal_is_not_a_name() -> None:
    """`"Q" & QUARTER([Date])` is a value, not a column name -- also Store Sales'.

    Which is why an argument counts as a name only when the whole of it is one
    string token. Accepting anything that merely *starts* with a string reads
    the `"Q"` as a name, swallows the argument after it as that name's formula,
    and reports a column called `Q` that exists in no model.
    """
    pairs = added_columns('ADDCOLUMNS(T, "Quarter", "Q" & QUARTER([Date]), "Year", 1)')
    assert pairs == [("Quarter", '"Q"&QUARTER([Date])'), ("Year", "1")]

    # Where the two rules actually part company: a string-leading expression
    # that lands in a position being tested rather than being consumed as
    # somebody's body. A name is *exactly* one string token and never merely
    # starts with one, so this argument names nothing and is passed over.
    assert added_columns('ADDCOLUMNS(T, "A", 1, "B" & [x])') == [("A", "1")]


def test_a_group_by_column_is_not_mistaken_for_a_name() -> None:
    """`SUMMARIZECOLUMNS` mixes group-by columns with name/expression pairs.

    Pairing by position would read the wrong halves of that argument list. The
    rule used instead -- a bare string literal names the argument after it --
    steps over the group-by columns without having to know where they sit,
    because a column reference is never a bare string.
    """
    pairs = added_columns(
        'SUMMARIZECOLUMNS(Item[Category], Store[Chain], "Total", SUM(Sales[Amount]))'
    )
    assert pairs == [("Total", "SUM(Sales[Amount])")]


def test_calendar_states_the_column_it_returns() -> None:
    """`CALENDAR` is defined to return one column called `Date`.

    That is the function's contract, not an inference about any one model, and
    it is how a date table comes by the column every hierarchy on it drills to.
    """
    assert calendar_column("CALENDAR(DATE(2012,1,1), DATE(2015,12,31))") == "Date"
    assert calendar_column("CALENDARAUTO()") == "Date"
    assert calendar_column("GENERATESERIES(0, 60, 1)") is None


def test_the_grain_column_comes_first_and_claims_no_formula() -> None:
    """`Date` leads because everything else is derived from it.

    Its expression is `None` on purpose: `CALENDAR` states that the column
    exists and what it is called, not a per-row formula for it, and writing one
    in would be inventing a definition the file does not contain.
    """
    columns = column_names('ADDCOLUMNS(CALENDAR(DATE(2012,1,1), DATE(2012,1,31)), "Year", YEAR([Date]))')
    assert columns[0] == ("Date", None)
    assert columns[1] == ("Year", "YEAR([Date])")


def test_a_name_assigned_twice_is_reported_once() -> None:
    assert [n for n, _ in column_names('ADDCOLUMNS(T, "A", 1, "A", 2)')] == ["A"]


def test_an_expression_that_will_not_parse_costs_the_columns_and_nothing_else() -> None:
    """Never an exception: a malformed table definition must not fail a model."""
    assert column_names("") == []
    assert column_names("ADDCOLUMNS(") == []
    # The name survives; the truncated body reads as "no formula recorded",
    # which is the same thing `None` means everywhere else here.
    assert column_names('ADDCOLUMNS(T, "A"') == [("A", None)]


# -- the model that made this necessary ----------------------------------------


def test_the_date_table_is_read_at_all(store) -> None:
    names = [t.name for t in store.user_tables()]
    assert "Date" in names, "the table the model's own descriptions point authors to"
    date = next(t for t in store.tables if t.name == "Date")
    assert date.is_calculated
    assert "CALENDAR" in date.dax_expression


def test_its_columns_come_with_their_formulas(store) -> None:
    columns = [c for c in store.columns if c.table == "Date"]
    assert len(columns) == 20
    by_name = {c.name: c for c in columns}
    assert by_name["Year"].expression == "YEAR([Date])"
    assert by_name["Fiscal Quarter"].expression == '"FQ"&QUARTER([Date])'
    # The grain column exists and states no formula, exactly as above.
    assert by_name["Date"].expression is None


def test_a_calculated_column_claims_no_data_type(store) -> None:
    """Power BI derives it at refresh from the expression's result.

    There is no type recorded in the file for these, and every consumer here
    reads "" as unknown. Filling in a plausible one would be inventing it.
    """
    for column in (c for c in store.columns if c.table == "Date"):
        assert column.data_type == ""


def test_the_hierarchies_now_resolve(store) -> None:
    """The eight complaints, gone -- because the model always had the columns."""
    graph = SemanticGraph(store)
    assert graph.unresolved == []

    drills = {h.name: [level.column for level in h.levels] for h in store.hierarchies}
    assert drills["Calendar Hierarchy"] == ["Year", "Quarter", "Month", "Date"]
    assert drills["Fiscal Hierarchy"] == [
        "Fiscal Year",
        "Fiscal Quarter",
        "Fiscal Month",
        "Date",
    ]
    held = {c.name for c in store.columns if c.table == "Date"}
    for path in drills.values():
        assert set(path) <= held


def test_power_bis_own_scratch_table_stays_out(store) -> None:
    """`ClusterMappingTable` is what the "find clusters" button leaves behind.

    It is a calculated table with a system flag -- and so is the hand-written
    `Date` table in the same file, which is why it is matched by name rather
    than by anything structural. Documenting it would put a machine's working
    notes in a business requirements document.
    """
    assert not any("ClusterMapping" in t.name for t in store.tables)


def test_a_measure_host_that_is_also_calculated_keeps_its_expression() -> None:
    """A what-if parameter table, which both hosts a measure and is built by DAX.

    It was already picked up as a measure host, so it is not added twice -- but
    the expression is the only record of where its rows come from, and it is
    attached rather than dropped on the floor.
    """
    if not SALES.exists():
        pytest.skip(f"model not present: {SALES}")
    model = PbixAdapter().extract(str(SALES))
    parameter = next(t for t in model.tables if t.name == "% Return Rate")
    assert parameter.dax_expression == "GENERATESERIES(0, 60, 1)"
    assert len([t for t in model.tables if t.name == "% Return Rate"]) == 1


def test_a_column_a_rename_hid_is_still_reported_rather_than_invented() -> None:
    """The honest limit of this, stated as a test so it cannot drift.

    `GENERATESERIES` returns a column called `Value`, and Power BI's what-if
    parameter feature renames it to match the table. The file records the new
    name nowhere this reader can see it, so the reference to it stays
    unresolved -- with a reason saying why. Writing `Value` in would add a
    column the model does not have *and* leave the real one unresolved, which
    is worse than admitting the gap.
    """
    if not SALES.exists():
        pytest.skip(f"model not present: {SALES}")
    model = PbixAdapter().extract(str(SALES))
    graph = SemanticGraph(model)
    assert [u.target for u in graph.unresolved] == ["% Return Rate[% Return Rate]"]
    assert "calculated" in graph.unresolved[0].reason
    assert not any(
        c.table == "% Return Rate" and c.name == "Value" for c in model.columns
    )

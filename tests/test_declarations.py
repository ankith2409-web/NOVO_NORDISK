"""What the model says about itself, beyond names and types.

A .pbix carries far more than the tables and formulas this project started by
reading, and the parts it does not read are invisible in exactly the way that
matters: nothing is missing from the output, the output is simply less true
than the file. Three rounds of that turned up here.

The map decided a column held a latitude by looking at its name, while the file
said so outright in `DataCategory`. The chart picker decided a column held an
image by sniffing its bytes, while the file said `ImageUrl`. And every figure
this tool printed was printed raw, under a sentence claiming Power BI's format
strings were "not exposed" by the file -- while `Net Sales` sat in the model's
own `Measure` table declaring `\\$#,0;-\\$#,0;\\$#,0`.

So these tests do one thing: assert that the declarations are read, against
real files, with counts. A count is what fails when a future reader quietly
stops surfacing a column -- which is how the calculated-column formulas went
missing once already.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter

SALES = Path("data/models/Sales_Returns_Sample.pbix")
STORE = Path("data/models/StoreSales.pbix")


def _model(path: Path):
    if not path.exists():
        pytest.skip(f"model not present: {path}")
    return PbixAdapter().extract(str(path))


@pytest.fixture(scope="module")
def sales():
    return _model(SALES)


@pytest.fixture(scope="module")
def store():
    return _model(STORE)


# -- measures ------------------------------------------------------------------


def test_measures_carry_the_format_string_the_file_declares(sales) -> None:
    """The finding this module was written for.

    PBIXRay surfaces five of the twenty columns the `Measure` table holds and
    `FormatString` is not among them, so the tool concluded the file did not
    have one and said so on screen. It has one for 37 of 58 measures here.
    """
    declared = [m for m in sales.measures if m.format_string]
    assert len(declared) == 37, f"{len(declared)} of {len(sales.measures)}"

    by_name = {m.name: m.format_string for m in sales.measures}
    assert by_name["Net Sales"] == "\\$#,0;-\\$#,0;\\$#,0"
    assert by_name["Net Sales Variance %"] == "0.0%;-0.0%;0.0%"


def test_every_measure_in_store_sales_declares_a_format(store) -> None:
    """A curated model states how all thirty-two of its metrics are written."""
    assert all(m.format_string for m in store.measures)
    assert len(store.measures) == 32


def test_a_measure_the_author_hid_is_read_as_hidden(sales, store) -> None:
    """Hidden is a statement of intent, and a reader deciding whether a measure
    is part of the solution wants it. None of the three samples hide one, so
    this asserts the field is populated rather than merely present."""
    for model in (sales, store):
        assert all(m.is_hidden is False for m in model.measures)


# -- tables --------------------------------------------------------------------


def test_tables_carry_the_authors_own_description(store) -> None:
    """Better than anything this tool can infer about what a table is for.

    Store Sales describes five of its seven tables, including one that warns
    authors off it -- which is precisely the kind of thing a BRD should carry
    and no amount of reading the schema would ever produce.
    """
    described = {t.name: t.description for t in store.tables if t.description}
    assert len(described) == 5
    assert "Do not use for new visuals" in described["Fiscal calendar"]


def test_a_table_the_author_hid_is_read_as_hidden(store) -> None:
    hidden = [t.name for t in store.tables if t.is_hidden]
    assert len(hidden) == 2, hidden


# -- columns -------------------------------------------------------------------


def test_columns_carry_what_the_model_says_they_are(sales) -> None:
    """`DataCategory` is the file answering a question this tool used to guess
    at by reading column names."""
    categories = {
        (c.table, c.name): c.data_category for c in sales.columns if c.data_category
    }
    assert categories[("Store", "Latitude")] == "Latitude"
    assert categories[("Store", "Longitude")] == "Longitude"
    assert len(categories) == 21, len(categories)


def test_columns_carry_their_declared_format_and_visibility(sales) -> None:
    assert sum(1 for c in sales.columns if c.format_string) == 28
    assert sum(1 for c in sales.columns if c.is_hidden) == 12


# -- what a missing reader would look like --------------------------------------


def test_reading_the_declarations_never_costs_the_model(sales) -> None:
    """The measure formats come through a private path in another library.

    A narrow reach, but a reach: if a future PBIXRay moves that furniture, the
    formats have to be what goes missing and not the model. This asserts the
    failure is contained by taking the path away.
    """
    adapter = PbixAdapter()

    class Broken:
        """Anything at all where the metadata store used to be."""

        def __getattr__(self, name):
            raise RuntimeError("no such thing")

    assert adapter._measure_declarations(Broken()) == {}


def test_an_implicit_measure_inherits_the_unit_it_aggregates(store) -> None:
    """`Sum of Sales` of a currency column is currency; `Count of Sales` is not.

    Carrying the format across a count would put a dollar sign in front of a
    number of rows, which is a plain lie about what the figure is.
    """
    from concordance.generate.implicit import from_report

    implicit = from_report(store)
    for measure in implicit:
        if measure.name.lower().startswith(("count of", "distinct count of")):
            assert not measure.format_string, measure.name


# -- and that the reading reaches the reader -------------------------------------


def test_the_api_sends_each_figure_the_way_the_file_asks_for_it(store) -> None:
    """Reading a declaration and not using it is the same as not reading it.

    Both forms travel: the rendered one is what makes a Concordance figure
    comparable to a Power BI card, and the raw one is what the SQL beside it
    actually returned. A page showing only the first would be uncheckable.
    """
    from concordance.graph.csg import SemanticGraph
    from concordance.web import api

    payload = api.values(api.ApiContext(graph=SemanticGraph(store)), {})
    if not payload["available"]:
        pytest.skip(payload["reason"])

    computed = [v for v in payload["values"] if v["value"] is not None]
    assert computed, "nothing computed, so nothing to format"
    formatted = [v for v in computed if v["shown"]]
    assert len(formatted) == len(computed), [
        v["measure"] for v in computed if not v["shown"]
    ]

    by_name = {v["measure"]: v for v in formatted}

    sales = by_name["Sales"]
    assert sales["shown"] == "$45,184,554"
    assert sales["compact"] == "$45.18M"
    assert sales["format"] == "a currency amount, no decimal places, grouped in thousands"
    # The figure the query returned is still there, unrounded.
    assert isinstance(sales["value"], float)

    # The example the old disclaimer used to name as the thing it could not do:
    # "Power BI renders a ratio like 0.42 as 42.29% using a format string this
    # file does not expose". This is that measure, on that file.
    margin = by_name["Gross Margin This Year %"]
    assert margin["shown"] == "42.29%"
    assert round(margin["value"], 4) == 0.4229

    # A negative renders through its own section -- brackets, not a minus.
    assert by_name["Total Sales Var"]["shown"] == "($1,080,649)"


def test_a_chart_slice_is_rendered_the_same_way_as_its_card(store) -> None:
    """A bar reading 881949 under a card reading $881,949 is one figure looking
    like two, which is the discrepancy this project exists to remove."""
    from concordance.graph.csg import SemanticGraph
    from concordance.web import api

    context = api.ApiContext(graph=SemanticGraph(store))
    payload = api.dashboard(context, {"measure": ["Sales"]})
    if not payload["available"]:
        pytest.skip(payload["reason"])

    assert payload["format"].startswith("a currency amount")
    drawn = [b for b in payload["breakdowns"] if b["slices"]]
    assert drawn, "nothing charted, so nothing to format"
    for breakdown in drawn:
        assert breakdown["shown"].startswith("$")
        for slice_ in breakdown["slices"]:
            assert slice_["shown"].startswith("$"), (breakdown["by"], slice_)


def test_a_business_document_states_a_percentage_as_a_percentage(store) -> None:
    """`0.4229` in a document a business reader is asked to sign off is a figure
    they cannot find anywhere in the report they know."""
    from concordance.generate import document as doc
    from concordance.generate.document import Kind
    from concordance.generate.evaluate import evaluate
    from concordance.graph.csg import SemanticGraph

    run = evaluate(store)
    if not run.available:
        pytest.skip(run.reason)

    figures = {v.measure: v.value for v in run.values if v.value is not None}
    built = doc.build(
        SemanticGraph(store), Kind.BUSINESS, figures=figures
    )
    text = doc.to_markdown(built)
    assert "*Currently:* **$22,051,952**" in text
    assert "*Currently:* **-4.67%**" in text
    # And the raw forms those replaced are gone from the figure lines.
    assert "*Currently:* **22,051,952**" not in text
    assert "*Currently:* **-0.0467**" not in text


# -- the order the author declared ----------------------------------------------


def test_a_column_carries_the_column_that_puts_it_in_order(sales, store) -> None:
    """`Calendar[Month]` is sorted by `Calendar[MonthSort]`, and the file says so.

    Two places in this project used to state the opposite -- that Power BI
    "records a column's display order in a sort-by column that this file's
    reader does not expose". It records it in `Column.SortByColumnID`.
    """
    by_column = {c.qualified_name: c.sort_by for c in sales.columns if c.sort_by}
    assert by_column["Calendar[Month]"] == "MonthSort"
    # And it is not only about time. `Details[Topic]` is a list of topics the
    # author put in a deliberate order that has nothing to do with dates.
    assert by_column["Details[Topic]"] == "TSort"

    assert {c.qualified_name: c.sort_by for c in store.columns if c.sort_by} == {
        "Fiscal calendar[FiscalMonth]": "Period",
        "Store[Opening month]": "Open Month No",
    }


def test_months_come_out_in_the_order_the_model_declares(store) -> None:
    """January to December, on a column no inference could have ordered.

    `Fiscal calendar` covers three years, so its "Jan" is January 2013 *and*
    January 2014 and there is no single date to anchor it at -- which is why
    the date-based fallback correctly declines it. The author declared
    `Period` as its sort column, and that answers the question outright.
    """
    from concordance.generate import breakdown as bd
    from concordance.generate.evaluate import open_data

    connection, _rows, reason = open_data(store)
    if connection is None:
        pytest.skip(reason)

    ordered = bd._anchors(store, connection, "Fiscal calendar", "FiscalMonth")
    assert [label for label, _ in sorted(ordered.items(), key=lambda kv: kv[1])] == [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]


def test_the_order_key_sorts_as_a_number_not_as_a_string(store) -> None:
    """`Slice.order` is compared as text here and in the browser, so `2` must
    not come after `10` -- which is exactly what month numbers would do."""
    from concordance.generate.breakdown import _sort_key

    keys = _sort_key([1, 2, 10, 12])
    assert sorted(keys.values()) == [keys[1], keys[2], keys[10], keys[12]]

    # Anything that is not a non-negative number is left as text rather than
    # padded into a false ordering.
    assert _sort_key(["b", "a"]) == {"b": "b", "a": "a"}
    assert _sort_key([-1, 2]) == {-1: "-1", 2: "2"}


def test_a_declared_order_beats_an_inferred_one(store) -> None:
    """A statement by the author outranks a measurement of the data.

    Proved by taking the declaration away: `Store[Opening month]` then falls
    back to the date rule, which for twelve months spread across a decade of
    openings correctly refuses to order them at all.
    """
    from dataclasses import replace

    from concordance.generate import breakdown as bd
    from concordance.generate.evaluate import open_data

    connection, _rows, reason = open_data(store)
    if connection is None:
        pytest.skip(reason)

    assert bd._anchors(store, connection, "Store", "Opening month")

    stripped = replace(
        store,
        columns=[
            replace(c, sort_by="") if c.table == "Store" else c for c in store.columns
        ],
    )
    assert bd._anchors(stripped, connection, "Store", "Opening month") == {}

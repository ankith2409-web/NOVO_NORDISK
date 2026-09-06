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
    # 23, not the 21 this once asserted: PBIXRay's published view of the
    # columns ends in `WHERE Type IN (1, 2)`, and a calculated table's columns
    # are type 4. Reading the model's own `Column` table instead found them.
    assert len(categories) == 23, len(categories)


def test_columns_carry_their_declared_format_and_visibility(sales) -> None:
    assert sum(1 for c in sales.columns if c.format_string) == 29
    assert sum(1 for c in sales.columns if c.is_hidden) == 14


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


# -- the same file saved the other way -------------------------------------------


TMDL_MODELS = sorted(Path("data/models").glob("*.SemanticModel"))


@pytest.fixture(scope="module")
def clinical():
    path = Path("data/models/ClinicalTrialSafety.SemanticModel")
    if not path.exists():
        pytest.skip(f"model not present: {path}")
    from concordance.adapters.tmdl import TmdlAdapter

    return TmdlAdapter().extract(str(path))


def test_a_tmdl_model_states_the_same_things_and_they_are_read(clinical) -> None:
    """The .pbix adapter and the TMDL adapter read one model saved two ways.

    This project already holds that rule for fingerprints -- "a guarantee that
    depends on which file format a model was saved in is not a guarantee" --
    and the declarations are no different. A map that finds its coordinates in
    one format and guesses in the other is exactly the thing the rule is
    against. Twenty-three of twenty-four measures here declare a format, and
    every one of them used to be dropped on the floor.
    """
    assert sum(1 for m in clinical.measures if m.format_string) == 23
    assert sum(1 for c in clinical.columns if c.format_string) == 8
    assert sum(1 for c in clinical.columns if c.is_hidden) == 2
    assert all(t.description for t in clinical.tables)


@pytest.mark.parametrize("path", TMDL_MODELS, ids=lambda p: p.stem)
def test_no_tmdl_model_silently_drops_its_formats(path: Path) -> None:
    """A count per model, so a reader that quietly stops surfacing one is
    noticed here rather than in a document six months later."""
    from concordance.adapters.tmdl import TmdlAdapter
    from concordance.generate.formats import render

    model = TmdlAdapter().extract(str(path))
    declared = [m for m in model.measures if m.format_string]
    assert len(declared) >= len(model.measures) - 1, [
        m.name for m in model.measures if not m.format_string
    ]
    # And what is read is read well enough to use: every measure format here
    # is a number picture, so all of them render.
    for measure in declared:
        assert render(1234.5, measure.format_string) is not None, measure.format_string


def test_a_table_the_author_marked_as_the_date_table_is_believed(clinical) -> None:
    """"Mark as date table" is the author answering the question outright.

    Without it this works the calendar out from the shape of the relationships,
    and on this model that reasoning declines -- so a file whose author had
    said which table was the calendar was reported as having none, and lost its
    year filter and every cut over time with it.
    """
    from dataclasses import replace

    from concordance.generate.breakdown import calendar_column

    assert [t.name for t in clinical.tables if t.data_category] == ["Calendar"]
    assert calendar_column(clinical) == ("Calendar", "Date")

    unmarked = replace(
        clinical, tables=[replace(t, data_category="") for t in clinical.tables]
    )
    assert calendar_column(unmarked) is None


# -- declarations that change what gets drawn ------------------------------------


def test_a_coordinate_is_not_offered_as_a_chart_dimension(sales) -> None:
    """"Net Sales by Latitude" was on the list of charts this tool offered.

    `Store[Latitude]` has fourteen distinct values and none of them is a URL or
    a picture, so every rule that had been keeping junk off the axis let it
    through. A latitude is where something is, not a category it belongs to,
    and the model says so in the same declaration the map reads to plot it.
    """
    from concordance.generate.breakdown import chartable
    from concordance.generate.evaluate import open_data

    connection, _rows, reason = open_data(sales)
    if connection is None:
        pytest.skip(reason)

    offered = {(t, c) for t, c, _ in chartable(sales, connection)}
    assert ("Store", "Latitude") not in offered
    assert ("Store", "Longitude") not in offered
    # And the column they belong to is still charted -- this removes the
    # coordinates, not the table.
    assert ("Store", "Store") in offered


def test_a_sort_helper_is_not_offered_beside_the_column_it_orders(sales) -> None:
    """`Calendar[MonthSort]` was offered as a chart of its own.

    The same six groups as `Calendar[Month]`, labelled 1 to 6 instead of by
    name. It exists to put that column in order; reading the sort-by
    declaration is what makes it recognisable as a mechanism rather than a
    dimension, without a rule about names ending in "Sort".
    """
    from concordance.generate.breakdown import chartable
    from concordance.generate.evaluate import open_data

    connection, _rows, reason = open_data(sales)
    if connection is None:
        pytest.skip(reason)

    offered = {(t, c) for t, c, _ in chartable(sales, connection)}
    assert ("Calendar", "MonthSort") not in offered
    assert ("Calendar", "Month") in offered


def test_columns_carry_whether_the_author_said_to_summarise_them(sales, store) -> None:
    """`SummarizeBy` is how an author marks a numeric column that is an
    identifier rather than a quantity -- which this project had been working
    out by looking for "ID" at the end of a name."""
    marked = [c for c in sales.columns if c.summarize_by == "none"]
    assert len(marked) == 68
    assert {c.summarize_by for c in sales.columns} <= {
        "", "default", "none", "sum", "min", "max", "count", "average", "distinctcount"
    }
    assert [c.qualified_name for c in store.columns if c.is_key] == [
        # `Date[Date]` is on a calculated table, so it was invisible until the
        # declarations stopped coming through a view that filters those out.
        "Date[Date]",
        "District[DM]",
        "Fiscal calendar[Month]",
        "Store[LocationID]",
    ]


def test_a_document_says_which_tool_wrote_the_file_it_read(sales) -> None:
    """"Read from a .pbix" is not checkable; a version number is.

    A document somebody is asked to sign should identify its source precisely
    enough to fetch again, and the file records the Desktop build that wrote it.
    """
    from concordance.generate import document as doc
    from concordance.generate.document import Kind
    from concordance.graph.csg import SemanticGraph

    assert sales.provenance["PBIDesktopVersion"].startswith("2.109.")
    built = doc.build(SemanticGraph(sales), Kind.BUSINESS)
    assert built.built_with == sales.provenance["PBIDesktopVersion"]
    assert "**Built with:** Power BI Desktop 2.109." in doc.to_markdown(built)


def test_a_source_with_no_provenance_says_nothing_rather_than_guessing(clinical) -> None:
    """A TMDL folder records no Desktop version, so the line is absent."""
    from concordance.generate import document as doc
    from concordance.generate.document import Kind
    from concordance.graph.csg import SemanticGraph

    built = doc.build(SemanticGraph(clinical), Kind.BUSINESS)
    assert built.built_with == ""
    assert "Built with" not in doc.to_markdown(built)


def test_time_intelligence_explains_the_hidden_date_tables(sales, store) -> None:
    """The most confusing thing about reading a .pbix is the pile of
    `LocalDateTable_*` in it. This is the switch that put them there."""
    assert sales.provenance["__PBI_TimeIntelligenceEnabled"] == "1"
    assert any(t.is_system for t in sales.tables)

    assert store.provenance["__PBI_TimeIntelligenceEnabled"] == "0"
    assert not any(t.name.startswith("LocalDateTable_") for t in store.tables)


# -- a control is not a subject area ---------------------------------------------


def test_a_what_if_parameter_is_recognised_as_one(sales) -> None:
    """`% Return Rate` is a slider, and the BRD called it a subject area.

    The property that says so is keyed by an untyped object id -- the same
    shape that makes translations unreadable here. The difference is that for a
    column the mapping is not a guess: the id is `Column.ID`, which this
    already reads to get format strings, so the join is real.
    """
    parameters = [c.qualified_name for c in sales.columns if c.is_parameter]
    assert parameters == ["% Return Rate[% Return Rate]"]
    assert [t.name for t in sales.tables if t.is_parameter] == ["% Return Rate"]


def test_the_scope_requirement_leaves_the_control_out_and_says_so(sales) -> None:
    """Dropping it silently would trade one wrong statement for another: a
    reader who knows the parameter exists needs to see it was considered."""
    from concordance.generate.document import Kind
    from concordance.generate.requirements import RequirementDeriver
    from concordance.graph.csg import SemanticGraph

    scope = next(
        r
        for r in RequirementDeriver(SemanticGraph(sales)).derive()
        if r.kind is Kind.BUSINESS and r.category == "Scope"
    )
    assert "13 subject areas" in scope.statement
    assert "% Return Rate" not in scope.statement
    assert "what-if parameter" in scope.rationale
    assert "% Return Rate" in scope.rationale


def test_a_grouped_column_names_the_column_it_groups(store) -> None:
    """`Item[Category (clusters) 2]` is groups of `Item[Category]`, not data of
    its own -- which this had been working out from "(clusters)" in the name."""
    grouped = {c.qualified_name: c.grouped_from for c in store.columns if c.grouped_from}
    assert grouped == {"Item[Category (clusters) 2]": "Category"}


def test_a_table_holding_a_parameter_beside_data_is_not_a_control(sales) -> None:
    """Only a table whose every column is a parameter is one.

    Otherwise a real table that happened to carry a parameter column would drop
    out of the subject areas entirely, which is a worse error than the one this
    fixes.
    """
    from dataclasses import replace

    from concordance.adapters.pbix import _parameter_tables

    assert _parameter_tables(sales.columns) == {"% Return Rate"}

    control = next(c for c in sales.columns if c.is_parameter)
    data = next(c for c in sales.columns if not c.is_parameter)
    beside = replace(data, table=control.table)
    assert _parameter_tables([*sales.columns, beside]) == set()

    # And a table with no columns at all is not a parameter by vacuous truth.
    assert _parameter_tables([]) == set()


# -- the audit, pinned -----------------------------------------------------------

#: Everything PBIXRay exposes that carries rows in the three sample files and
#: is still not named anywhere in the adapter, with why. Pinned as a test
#: because this list is how the gaps in this module were found in the first
#: place -- by enumerating the source against the reader rather than by any
#: failing test -- and a list that lives only in somebody's head grows back.
UNREAD: dict[str, str] = {
    # Calculated-table DAX, which the adapter reads from partitions instead
    # (partition type 2). The only tables this frame adds are ones Power BI
    # writes for itself, asserted below.
    "dax_tables": "read through partitions instead",
    # Power BI's own per-column cardinality and byte sizes. `chartable` counts
    # distinct values in the data itself, which is the same number from the
    # source that will still be right after a refresh.
    "statistics": "storage statistics, and the data answers the same question",
    # SummarizationSetBy, PBI_FormatHint, TemplateId, PBI_ResultType,
    # PBI_NavigationStepName. The two that carry meaning are read from better
    # places: the summarization from `Column.SummarizeBy`, and the format hint
    # is superseded by the format string itself.
    "tmschema_annotations": "internal, and the meaningful parts are read elsewhere",
    # One storage structure per column, with no author intent in it.
    "tmschema_attribute_hierarchies": "storage internals",
    # Culture, collation, DiscourageImplicitMeasures and the parallelism
    # settings. Nothing here changes what a figure means.
    "tmschema_model": "engine settings, not model content",
}


def test_every_unread_source_is_unread_on_purpose() -> None:
    """The enumeration that found every gap in this module, kept running.

    A source that starts carrying rows, or one this adapter quietly stops
    reading, shows up here as a name with no entry in `UNREAD` -- which is the
    signal to go and look at it, exactly as happened for `tmschema_columns`.
    """
    import re

    from pbixray import PBIXRay

    source = Path("concordance/adapters/pbix.py").read_text()
    paths = [Path(f"data/models/{n}.pbix") for n in ("Sales_Returns_Sample", "StoreSales")]
    if not all(p.exists() for p in paths):
        pytest.skip("sample models not present")

    carries_rows: set[str] = set()
    for path in paths:
        raw = PBIXRay(str(path))
        for attribute in dir(raw):
            if attribute.startswith("_") or attribute in {"close", "get_table", "iter_table"}:
                continue
            try:
                frame = getattr(raw, attribute)
                if len(frame):
                    carries_rows.add(attribute)
            except Exception:  # noqa: BLE001 - not every attribute is a frame
                continue

    unread = {
        a
        for a in carries_rows
        if not re.search(rf'"{a}"|\'{a}\'|\braw\.{a}\b', source)
    }
    assert unread == set(UNREAD), {
        "newly unread": sorted(unread - set(UNREAD)),
        "now read, drop from UNREAD": sorted(set(UNREAD) - unread),
    }


def test_the_only_tables_dax_tables_adds_are_ones_power_bi_wrote(sales) -> None:
    """Justifies the `dax_tables` entry above.

    The adapter reads calculated-table DAX from partitions. If that ever missed
    a table an author wrote, a calculated table's only statement of what it
    contains would go unrecorded -- so the difference between the two sources
    is asserted rather than assumed.
    """
    from pbixray import PBIXRay

    from concordance.adapters.pbix import PbixAdapter

    raw = PBIXRay(str(SALES))
    missed = set(raw.dax_tables["TableName"]) - set(PbixAdapter()._calculated_tables(raw))
    system = {t.name for t in sales.tables if t.is_system}
    assert missed and missed <= system, missed


# -- where a table's rows actually are -------------------------------------------


def test_every_table_in_these_files_stores_its_own_rows(sales, store) -> None:
    """Which is why the tool's central promise holds for them.

    Every figure it produces comes from running a measure's own SQL against
    the model's own rows. That is only true of an Import table.
    """
    for model in (sales, store):
        assert {t.storage_mode for t in model.user_tables()} == {"import"}
        assert not [
            g for g in model.coverage_gaps if "does not carry" in g.feature
        ]


def test_a_directquery_table_is_reported_as_rows_this_file_does_not_have() -> None:
    """None of the samples uses DirectQuery, so this is the synthetic case.

    It matters because of how it fails without this. A measure over a
    DirectQuery table has no rows to run against, and the query comes back
    "table does not exist" -- which reads as a defect in this tool rather than
    as the file saying where its data lives.
    """
    from concordance.adapters.pbix import PbixAdapter
    from concordance.model import SemanticModel, Table

    model = SemanticModel(name="Remote", source_path="x.pbix", source_type="pbix")
    model.tables = [
        Table(name="Sales", fingerprint="a", storage_mode="directquery"),
        Table(name="Ledger", fingerprint="b", storage_mode="dual"),
        Table(name="Product", fingerprint="c", storage_mode="import"),
        # A hidden date table Power BI wrote is not something to warn about.
        Table(name="LocalDateTable_x", fingerprint="d", storage_mode="directquery",
              is_system=True),
    ]

    class _Nothing:
        def __getattr__(self, name):
            raise AttributeError(name)

    gaps = PbixAdapter()._coverage_gaps(_Nothing(), model)
    remote = next(g for g in gaps if "does not carry" in g.feature)
    assert remote.count == 2
    assert "Ledger, Sales" in remote.reason
    assert "Product" not in remote.reason
    assert "LocalDateTable_x" not in remote.reason
    assert "limit of this tool" in remote.reason


def test_a_partitions_storage_mode_is_read_from_its_own_numbering() -> None:
    """Power BI writes the mode as an integer, and 2 -- "default" -- appears on
    the engine's own per-column storage partitions, where it says nothing about
    a table an author made."""
    import pandas as pd

    from concordance.adapters.pbix import PbixAdapter

    class _Raw:
        tmschema_partitions = pd.DataFrame(
            [
                {"TableName": "Sales", "Type": 4, "Mode": 0},
                {"TableName": "Live", "Type": 4, "Mode": 1},
                {"TableName": "Either", "Type": 4, "Mode": 3},
                {"TableName": "Derived", "Type": 2, "Mode": 0},
                # The engine's own storage partition, which must not overwrite
                # the table's real mode.
                {"TableName": "H$Sales (1)$Amount (2)", "Type": 3, "Mode": 2},
            ]
        )

    assert PbixAdapter()._storage_modes(_Raw()) == {
        "sales": "import",
        "live": "directquery",
        "either": "dual",
        "derived": "import",
    }


#: The TMDL side of the same audit: property names that appear in the
#: .SemanticModel fixtures and are not read, with why. `mode` and `formatString`
#: used to be on this list; enumerating is what took them off it.
TMDL_UNREAD: dict[str, str] = {
    # A column's name in the source system. Real lineage where it differs from
    # the model's name -- and it differs nowhere across all six sample models,
    # so reading it would add a field that is empty everywhere and testable
    # nowhere. Worth taking off this list the day a file renames one.
    "sourceColumn": "identical to the column name in every sample",
    # Engine settings. The .pbix side reads PBIDesktopVersion as provenance,
    # which answers the same question ("what wrote this") more legibly.
    "compatibilityLevel": "engine version, and provenance is read from elsewhere",
    "defaultPowerBIDataSourceVersion": "engine setting, not model content",
    # Which icon set a KPI draws its status with. Presentation; the KPI's
    # target and status *descriptions* are read, which is the meaning.
    "statusGraphic": "presentation, and the KPI's descriptions are read",
    # Not properties at all -- these are M and DAX keywords inside expressions,
    # which the adapter keeps whole rather than parsing into properties.
    "let": "M keyword inside an expression",
    "in": "M keyword inside an expression",
    "Source": "an M step name inside an expression",
    "Promoted": "an M step name inside an expression",
    "Indexed": "an M step name inside an expression",
    "RETURN": "DAX keyword inside an expression",
    "DAY": "DAX keyword inside an expression",
}


def test_every_unread_tmdl_property_is_unread_on_purpose() -> None:
    """The same enumeration, for the other file format.

    Held to the same standard on purpose: this project's rule is that a
    guarantee depending on which format a model was saved in is not a
    guarantee, and an audit that only ever ran against .pbix would let the
    TMDL adapter drift exactly as far as it had drifted before.
    """
    import re

    source = Path("concordance/adapters/tmdl.py").read_text()
    files = list(Path("data/models").glob("*.SemanticModel/definition/**/*.tmdl"))
    if not files:
        pytest.skip("no .SemanticModel fixtures present")

    seen: set[str] = set()
    for path in files:
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("///", "ref ")):
                continue
            named = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[:=]", stripped)
            if named:
                seen.add(named.group(1))
            elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped):
                seen.add(stripped)

    unread = {p for p in seen if not re.search(rf'"{p}"|\'{p}\'', source)}
    assert unread == set(TMDL_UNREAD), {
        "newly unread": sorted(unread - set(TMDL_UNREAD)),
        "now read, drop from TMDL_UNREAD": sorted(set(TMDL_UNREAD) - unread),
    }


def test_no_sample_model_renames_a_column_from_its_source() -> None:
    """Justifies the `sourceColumn` entry above, in both formats.

    If one ever does, that is a fact a lineage document should carry, and this
    is where it should be noticed.
    """
    from pbixray import PBIXRay

    renamed: list[str] = []
    for name in ("Sales_Returns_Sample", "StoreSales", "Supply_Chain_Sample"):
        path = Path(f"data/models/{name}.pbix")
        if not path.exists():
            continue
        frame = PBIXRay(str(path)).tmschema_columns
        # `notna` rather than a truth test: a missing SourceColumn arrives as
        # NaN, and NaN is truthy.
        stated = frame[frame.SourceColumn.notna() & frame.Name.notna()]
        # And the engine's own storage columns, which are not an author's.
        authored = stated[
            ~stated.TableName.str.startswith(
                ("DateTableTemplate_", "LocalDateTable_", "H$")
            )
        ]
        for row in authored[authored.SourceColumn != authored.Name].itertuples():
            renamed.append(f"{row.TableName}[{row.Name}] <- {row.SourceColumn}")
    assert renamed == [], renamed


# -- does it read everything? ----------------------------------------------------
#
# The tests above pin counts, which catches a regression but not an omission
# nobody thought of. These derive the answer from the file instead, so a column
# the reader has never seen still has to turn up.

PBIX_MODELS = ["Sales_Returns_Sample", "StoreSales", "Supply_Chain_Sample"]


def _store(path: Path):
    """The model's own metadata tables, which PBIXRay publishes a view of."""
    from pbixray import PBIXRay

    return PBIXRay(str(path))._metadata.source._db


@pytest.mark.parametrize("name", PBIX_MODELS)
def test_every_column_the_model_declares_is_extracted(name: str) -> None:
    """Counted against the file's own `Column` table, not a hand-written number.

    This is the test that would have caught the calculated-table gap on its own.
    PBIXRay's published view of the columns ends in `WHERE Type IN (1, 2)` and a
    calculated table's columns are type 4, so 22 columns of Store Sales' `Date`
    table — the table that file's own description tells authors to use — were
    absent from every declaration this tool read.
    """
    path = Path(f"data/models/{name}.pbix")
    if not path.exists():
        pytest.skip(f"model not present: {path}")

    model = PbixAdapter().extract(str(path))
    rows = _store(path).query(
        "SELECT t.Name AS tbl, COALESCE(c.ExplicitName, c.InferredName) AS col "
        "FROM [Column] c JOIN [Table] t ON c.TableID = t.ID"
    )
    extracted = {(c.table.casefold(), c.name.casefold()) for c in model.columns}
    # The engine's own storage tables are not an author's columns.
    declared = {
        (str(r.tbl).casefold(), str(r.col).casefold())
        for r in rows.itertuples()
        if str(r.col) != "nan"
        and not str(r.tbl).startswith(("H$", "R$", "U$"))
        and not str(r.col).startswith("RowNumber-")
    }
    assert declared <= extracted, sorted(declared - extracted)


@pytest.mark.parametrize("name", PBIX_MODELS)
def test_every_declaration_matches_what_the_file_says(name: str) -> None:
    """Reading a column is not the same as reading what the file says about it.

    Format string, hidden, key and data category are checked cell by cell
    against the source, so a reader that finds the column and drops its
    declarations fails here rather than producing a quietly poorer document.
    """
    import pandas as pd

    path = Path(f"data/models/{name}.pbix")
    if not path.exists():
        pytest.skip(f"model not present: {path}")

    model = PbixAdapter().extract(str(path))
    by_key = {(c.table.casefold(), c.name.casefold()): c for c in model.columns}
    rows = _store(path).query(
        "SELECT t.Name AS tbl, COALESCE(c.ExplicitName, c.InferredName) AS col, "
        "c.IsHidden AS hidden, c.IsKey AS iskey, c.FormatString AS fmt, "
        "c.DataCategory AS cat "
        "FROM [Column] c JOIN [Table] t ON c.TableID = t.ID"
    )

    def said(value) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        return str(value).strip()

    checked = 0
    for row in rows.itertuples():
        column = by_key.get((str(row.tbl).casefold(), str(row.col).casefold()))
        if column is None:
            continue
        checked += 1
        where = f"{row.tbl}[{row.col}]"
        assert column.is_hidden == (row.hidden == 1), where
        assert column.is_key == (row.iskey == 1), where
        assert column.format_string == said(row.fmt), where
        assert column.data_category == said(row.cat), where
    assert checked >= len(model.columns), (checked, len(model.columns))


@pytest.mark.parametrize("name", PBIX_MODELS)
def test_a_column_name_survives_extraction_exactly(name: str) -> None:
    """A name is an identity, and trimming one is a rename.

    `Supply Analytics[Product ]` really does end in a space. Stripping it left
    the model holding `Product` while the data held `Product `, so the query
    this tool generated failed with "column not found", the report tile that
    uses it could not be bound, and the implicit measure over it was dropped.
    One trailing space, three wrong answers — and nothing said so.
    """
    from pbixray import PBIXRay

    path = Path(f"data/models/{name}.pbix")
    if not path.exists():
        pytest.skip(f"model not present: {path}")

    raw = PBIXRay(str(path))
    model = PbixAdapter().extract(str(path))
    for table in raw.tables:
        in_data = list(raw.get_table(table).columns)
        in_model = [c.name for c in model.columns if c.table == table]
        assert set(in_data) <= set(in_model), {
            "table": table,
            "in the data, not the model": sorted(set(in_data) - set(in_model)),
        }


@pytest.mark.parametrize("name", PBIX_MODELS)
def test_every_column_can_be_queried_by_the_name_the_model_holds(name: str) -> None:
    """The end of the chain, and the only part a reader ever sees fail.

    Everything above is bookkeeping until a query runs. This asks the loaded
    data for every column the model claims, by the name the model holds, and a
    single mismatch anywhere upstream surfaces here as a binder error.
    """
    from concordance.generate.evaluate import open_data

    path = Path(f"data/models/{name}.pbix")
    if not path.exists():
        pytest.skip(f"model not present: {path}")

    model = PbixAdapter().extract(str(path))
    connection, _rows, reason = open_data(model)
    if connection is None:
        pytest.skip(reason)

    stored = {t.name for t in model.tables if not t.is_calculated or True}
    failed: list[str] = []
    for column in model.columns:
        if column.table not in stored or column.expression is not None:
            continue  # a calculated column may not be materialised
        table = column.table.replace('"', '""')
        field = column.name.replace('"', '""')
        try:
            connection.execute(f'SELECT "{field}" FROM "{table}" LIMIT 1').fetchone()
        except Exception as exc:  # noqa: BLE001 - the failure is the finding
            failed.append(f"{column.qualified_name}: {str(exc).splitlines()[0]}")
    assert failed == [], failed


@pytest.mark.parametrize("name", PBIX_MODELS)
def test_every_tile_field_binds_to_something_in_the_model(name: str) -> None:
    """A tile bound to a field the model does not carry is reported as such.

    That reporting is right, and it should be rare. When it is not rare it
    means the two readers disagree about a name — which is exactly what the
    trailing space did to `Supply Analytics[Product ]`.
    """
    path = Path(f"data/models/{name}.pbix")
    if not path.exists():
        pytest.skip(f"model not present: {path}")

    model = PbixAdapter().extract(str(path))
    known = {(c.table.casefold(), c.name.casefold()) for c in model.columns}
    known |= {(m.table.casefold(), m.name.casefold()) for m in model.measures}
    measures_by_name = {m.name.casefold() for m in model.measures}

    unbound = [
        f"{visual.page} · {field.qualified_name}"
        for visual in model.visuals()
        for field in visual.fields
        if (field.table.casefold(), field.name.casefold()) not in known
        and field.name.casefold() not in measures_by_name
    ]
    # One, and it is real: a Q&A visual on the Returns page still asks for
    # `Sales[Dates]`, and that column does not exist under any name — not as a
    # column, a hierarchy or a measure. The report outlived the field. The tool
    # already says so on the tile rather than inventing a binding, so what this
    # guards is that the list does not grow: a *new* entry means the two
    # readers have started disagreeing about a name.
    assert unbound == (["Returns · Sales[Dates]"] if name == "Sales_Returns_Sample" else []), unbound


# -- how old is the number? ------------------------------------------------------


def test_a_table_records_when_its_rows_were_loaded(sales) -> None:
    """Every figure this tool produces comes from rows stored in the file.

    That is what makes them checkable, and it also makes them exactly as old as
    the last refresh — which the file records per partition and nobody was
    reading.
    """
    dated = {t.name: t.refreshed_at for t in sales.user_tables() if t.refreshed_at}
    assert len(dated) == 16
    assert all(when.startswith("2019-") for when in dated.values()), dated


def test_a_never_refreshed_partition_is_not_dated_to_1699(store) -> None:
    """The engine writes a seventeenth-century sentinel for "never loaded".

    Five of Store Sales' tables carry it. Reporting it as a refresh would date
    the data to before the invention of the spreadsheet.
    """
    never = {"Store", "Sales", "Item", "Fiscal calendar", "District"}
    for table in store.user_tables():
        if table.name in never:
            assert table.refreshed_at == "", (table.name, table.refreshed_at)
    # The calculated tables were genuinely recomputed, and say so.
    assert any(t.refreshed_at for t in store.user_tables() if t.is_calculated)


def test_data_as_of_ignores_tables_the_engine_merely_recomputed(store, sales) -> None:
    """A calculated table's timestamp is when it was last recalculated.

    Rolling it into "when was this data loaded" would have Store Sales
    reporting data as of 2026 on the strength of a recalculation, while the
    five tables actually holding its sales have never recorded a refresh at
    all. Saying nothing is the honest answer there.
    """
    assert store.data_as_of() == ""
    assert sales.data_as_of() == "2019-12-10 18:44:09"


def test_a_document_says_how_old_its_figures_are(sales) -> None:
    """A seven-year gap between the data and the signature is worth a line."""
    from concordance.generate import document as doc
    from concordance.generate.document import Kind
    from concordance.graph.csg import SemanticGraph

    built = doc.build(SemanticGraph(sales), Kind.BUSINESS)
    assert built.data_as_of == "2019-12-10 18:44:09"
    assert "**Data loaded:** 2019-12-10" in doc.to_markdown(built)


# -- the audit, one level deeper -------------------------------------------------
#
# `UNREAD` above asks which *sources* go unread. This asks which *fields* do,
# inside the sources that are read — which is where the calculated-table
# columns and the refresh times were hiding, one level below where anybody had
# looked.

#: Fields the adapter does not read, and why. Grouped by the frame they sit in.
UNREAD_FIELDS: dict[str, dict[str, str]] = {
    "relationships": {
        # How many distinct keys each side has. A storage statistic; the data
        # answers the same question and stays right after a refresh.
        "FromKeyCount": "cardinality statistics",
        "ToKeyCount": "cardinality statistics",
    },
    "tmschema_columns": {
        # The declared type. Checked against what the reader infers from the
        # data across all three files: they never disagree — every column
        # either declares the matching type or declares 1, which is the
        # engine's "automatic", i.e. the author never set one.
        "DataType": "never disagrees with the type inferred from the data",
        # Constraints on the values rather than statements about meaning.
        "IsUnique": "a constraint, not a definition",
        "IsNullable": "a constraint, not a definition",
        # Identical to the column's own name in all six sample models.
        "SourceColumn": "identical to the column name everywhere",
        # Engine internals: storage hints, ids, ordering and timestamps.
        "TableID": "internal id",
        "IsAvailableInMDX": "engine internal",
        "EncodingHint": "storage hint",
        "LineageTag": "internal id",
        "SourceLineageTag": "internal id",
        "DisplayOrdinal": "field-list ordering",
        "ModifiedTime": "timestamp",
        "StructureModifiedTime": "timestamp",
    },
    "tmschema_extended_properties": {"ModifiedTime": "timestamp"},
    "tmschema_hierarchies": {
        "HideMembers": "presentation",
        "TableID": "internal id",
        "State": "engine internal",
        "HierarchyStorageID": "internal id",
        "LineageTag": "internal id",
        "SourceLineageTag": "internal id",
        "ModifiedTime": "timestamp",
        "StructureModifiedTime": "timestamp",
    },
    "tmschema_levels": {
        # Both are second spellings of a key already read by the other name.
        "HierarchyName": "the level is keyed by HierarchyID instead",
        "ColumnID": "the column is read by name instead",
        "LineageTag": "internal id",
        "SourceLineageTag": "internal id",
        "ModifiedTime": "timestamp",
    },
    "tmschema_linguistic_metadata": {
        # The synonyms themselves are read, counted and deliberately not used
        # as vocabulary — see UNREAD. Which culture they belong to only matters
        # to a consumer that uses them.
        "CultureID": "internal id",
        "CultureName": "only meaningful if the synonyms were used",
        "ContentType": "encoding of a payload that is parsed anyway",
        "ModifiedTime": "timestamp",
    },
    "tmschema_partitions": {
        "TableID": "internal id",
        "State": "engine internal",
        "DataView": "engine internal",
        "DataSourceID": "internal id",
        "SystemFlags": "engine internal",
        "ModifiedTime": "timestamp",
    },
    "tmschema_tables": {
        "IsPrivate": "engine internal",
        "ShowAsVariationsOnly": "marks the generated date tables, already detected by name",
        "LineageTag": "internal id",
        "SourceLineageTag": "internal id",
        "ModifiedTime": "timestamp",
        "StructureModifiedTime": "timestamp",
    },
    "tmschema_variations": {
        "ColumnID": "the column is read by name instead",
        "RelationshipID": "internal id",
        "DefaultColumnID": "internal id",
    },
}


def test_every_unread_field_is_unread_on_purpose() -> None:
    """The source audit one level down.

    `UNREAD` asks which sources go unread; this asks which *fields* do inside
    the ones that are read. That is where the calculated-table columns were
    hiding — the source was being read, and a `WHERE Type IN (1, 2)` inside it
    was dropping a quarter of Store Sales' columns — and where the refresh
    times were too.
    """
    import re

    from pbixray import PBIXRay

    path = Path("data/models/Sales_Returns_Sample.pbix")
    if not path.exists():
        pytest.skip(f"model not present: {path}")

    source = Path("concordance/adapters/pbix.py").read_text()
    named = {a or b for a, b in re.findall(r'raw\.(\w+)|_safe\(raw, "(\w+)"\)', source)}
    raw = PBIXRay(str(path))

    unread: dict[str, set[str]] = {}
    for attribute in sorted(named - {""}):
        try:
            frame = getattr(raw, attribute)
            if not len(frame):
                continue
            columns = list(frame.columns)
        except Exception:  # noqa: BLE001 - not every attribute is a frame
            continue
        missing = {c for c in columns if f'"{c}"' not in source}
        if missing:
            unread[attribute] = missing

    for frame, fields in unread.items():
        stated = set(UNREAD_FIELDS.get(frame, {}))
        assert fields == stated, {
            "frame": frame,
            "newly unread": sorted(fields - stated),
            "now read, drop from UNREAD_FIELDS": sorted(stated - fields),
        }
    assert set(unread) == set(UNREAD_FIELDS), {
        "frames not accounted for": sorted(set(unread) - set(UNREAD_FIELDS)),
        "fully read now": sorted(set(UNREAD_FIELDS) - set(unread)),
    }


def test_the_declared_column_type_never_contradicts_the_data() -> None:
    """Justifies the `DataType` entry above, rather than asserting it.

    Every column either declares the type the reader infers from the data, or
    declares 1 — the engine's "automatic", which is the author never having set
    one. If a file ever turns up where they disagree, the declaration is the
    better answer and this is where that should be noticed.
    """
    from pbixray import PBIXRay

    #: Power BI's own numbering, for the types these files use.
    equivalent = {
        2: {"string"},
        6: {"Int64"},
        8: {"Float64"},
        9: {"datetime64[ns]"},
        17: {"bytes"},
    }
    for name in PBIX_MODELS:
        path = Path(f"data/models/{name}.pbix")
        if not path.exists():
            continue
        raw = PBIXRay(str(path))
        merged = raw.schema.merge(
            raw.tmschema_columns,
            left_on=["TableName", "ColumnName"],
            right_on=["TableName", "Name"],
            how="inner",
        )
        for row in merged.itertuples():
            declared = int(row.DataType) if row.DataType == row.DataType else 1
            if declared == 1:
                continue  # the author set none; the data decides
            assert str(row.PandasDataType) in equivalent.get(declared, set()), (
                f"{row.TableName}[{row.ColumnName}]: declared {declared}, "
                f"data is {row.PandasDataType}"
            )

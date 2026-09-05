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
    assert len(marked) == 65
    assert {c.summarize_by for c in sales.columns} <= {
        "", "default", "none", "sum", "min", "max", "count", "average", "distinctcount"
    }
    assert [c.qualified_name for c in store.columns if c.is_key] == [
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

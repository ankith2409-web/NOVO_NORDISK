"""Translating DAX into SQL, and refusing to when that would be a lie.

The tests that matter here are the ones at the bottom: they build a tiny
warehouse with hand-computed answers, run the *generated* SQL against it, and
compare. A translator that produces plausible-looking SQL is worth nothing --
the only evidence that it works is a number coming back correct, so that is
what is asserted.

The rest guard the boundary. Every measure this refuses must refuse for a
stated reason naming the construct, because "no SQL" with no explanation is
indistinguishable from a bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.generate.sql import (
    DIALECTS,
    Status,
    to_dialect,
    translate,
    translate_all,
)

MODEL = Path("data/models/QualityControl.SemanticModel")
SITE = ("Site[SiteName]",)


@pytest.fixture(scope="module")
def model():
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return TmdlAdapter().extract(str(MODEL))


@pytest.fixture(scope="module")
def measures(model):
    return {m.name: m for m in model.measures}


def sql_for(model, measures, name, grain=SITE):
    result = translate(model, measures[name], grain)
    assert result.translated, f"{name} did not translate: {result.reason}"
    return result.sql


# -- what it translates --------------------------------------------------------

def test_a_row_count_becomes_count_star(model, measures):
    """COUNT(column) would drop rows where that column is null; COUNTROWS does not."""
    assert "COUNT(*)" in sql_for(model, measures, "Batches Manufactured")


def test_divide_guards_the_denominator(model, measures):
    """DAX's DIVIDE returns the alternate result on zero. NULLIF plus COALESCE
    is the only spelling that reproduces both halves of that."""
    sql = sql_for(model, measures, "OOS Rate")
    assert "NULLIF(" in sql
    assert "COALESCE(" in sql


def test_calculate_filters_one_aggregate_not_the_query(model, measures):
    """The whole point of FILTER (WHERE ...).

    `Calibration Compliance %` subtracts a filtered count from an unfiltered
    one. Rendering CALCULATE as a shared WHERE clause would filter both, and
    the measure would silently evaluate to zero.
    """
    sql = sql_for(model, measures, "Calibration Compliance %")
    assert "FILTER (WHERE" in sql
    assert "WHERE" not in sql.split("FILTER (WHERE")[0], "a query-level WHERE crept in"


def test_a_join_path_is_followed_across_two_hops(model, measures):
    """TestResult reaches Site only through Batch. Both joins must appear, and
    in an order that keeps each ON clause resolvable."""
    sql = sql_for(model, measures, "OOS Rate")
    assert sql.index('JOIN "Batch"') < sql.index('JOIN "Site"')


def test_a_measure_reference_is_inlined(model, measures):
    """[OOS Rate] is defined over two other measures; neither name may survive
    into the SQL, because the warehouse has never heard of them."""
    sql = sql_for(model, measures, "OOS Rate")
    assert "OOS Results" not in sql.replace('AS "OOS Rate"', "")


def test_a_calculated_column_is_recomputed_not_selected(model, measures):
    """`Days To Release` has no storage in the source. Selecting it would fail
    against the real tables, so its own expression is inlined instead."""
    sql = sql_for(model, measures, "Average Days To Release")
    assert "Days To Release" not in sql.replace('AS "Average Days To Release"', "")
    assert "DATE_DIFF" in sql


def test_var_return_is_substituted(model, measures):
    """A DAX variable is evaluated in the filter context where it was declared,
    which inlining reproduces and a SQL alias would not."""
    sql = sql_for(model, measures, "Dissolution Pass Rate")
    assert "DissolutionTests" not in sql


def test_switch_true_becomes_case(model, measures):
    sql = sql_for(model, measures, "Quality Status")
    assert sql.count("WHEN") >= 3
    assert "ELSE" in sql


def test_the_grain_is_grouped_and_ordered(model, measures):
    sql = sql_for(model, measures, "Batch Yield %")
    assert 'GROUP BY "Site"."SiteName"' in sql
    assert 'ORDER BY "Site"."SiteName"' in sql


def test_no_grain_means_one_row(model, measures):
    """A measure with no filter context is a single number, so no GROUP BY."""
    sql = sql_for(model, measures, "Batch Yield %", grain=())
    assert "GROUP BY" not in sql


# -- what it refuses, and why --------------------------------------------------

@pytest.mark.parametrize(
    "measure,construct",
    [
        ("OOS Results PM", "PREVIOUSMONTH"),
        ("Instrument Failure Rank", "ALL"),
        ("Batches By Release Date", "USERELATIONSHIP"),
    ],
)
def test_undecidable_measures_are_blocked_by_name(model, measures, measure, construct):
    """Blocked, not broken. The construct is named so a reader can tell the
    difference between "we cannot" and "we did not try"."""
    result = translate(model, measures[measure], SITE)
    assert result.status is Status.BLOCKED
    assert result.blocked_by == construct
    assert result.sql == ""
    assert construct in result.reason


def test_nothing_is_left_merely_unsupported(model):
    """Every failure should be a genuine impossibility.

    An UNSUPPORTED result means this translator has a gap, not that DAX does.
    Keeping this at zero is what stops the coverage number being flattering.
    """
    gaps = [r for r in translate_all(model, SITE) if r.status is Status.UNSUPPORTED]
    assert gaps == [], f"translator gaps: {[(r.measure, r.reason) for r in gaps]}"


def test_a_blocked_measure_never_returns_sql(model):
    """The failure mode this whole design exists to prevent: SQL that parses,
    runs, and quietly answers a different question."""
    for result in translate_all(model, SITE):
        if not result.translated:
            assert not result.sql


# -- other platforms -----------------------------------------------------------

def test_snowflake_gets_its_own_idiom(model, measures):
    """FILTER (WHERE ...) is not Snowflake syntax; COUNT_IF is."""
    snowflake = to_dialect(sql_for(model, measures, "OOS Rate"), "snowflake")
    assert "COUNT_IF" in snowflake
    assert "FILTER (WHERE" not in snowflake


def test_databricks_quotes_with_backticks(model, measures):
    assert "`Site`" in to_dialect(sql_for(model, measures, "Batch Yield %"), "databricks")


def test_every_declared_platform_transpiles(model, measures):
    sql = sql_for(model, measures, "Batch Yield %")
    for platform in DIALECTS:
        assert to_dialect(sql, platform).strip(), f"{platform} produced nothing"


def test_an_unknown_dialect_returns_the_input(model, measures):
    """Better to hand back SQL that runs on one platform than SQL that runs
    nowhere."""
    sql = sql_for(model, measures, "Batch Yield %")
    assert to_dialect(sql, "no-such-warehouse") == sql


# -- the part that proves it works ---------------------------------------------

@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    """A warehouse small enough to compute by hand.

    North: 2 batches, 90+80 of 100+100 yield, 10 tests of which 2 failed.
    South: 1 batch,   50 of 100 yield,        5 tests of which none failed.
    """
    duckdb = pytest.importorskip("duckdb")
    path = tmp_path_factory.mktemp("wh") / "proof.duckdb"
    con = duckdb.connect(str(path))
    con.execute('CREATE TABLE "Site"("SiteID" INT,"SiteName" VARCHAR)')
    con.execute("""INSERT INTO "Site" VALUES (1,'North'),(2,'South')""")
    con.execute(
        'CREATE TABLE "Batch"("BatchID" INT,"SiteID" INT,"ProductID" INT,'
        '"ActualYield" INT,"TheoreticalYield" INT,"Status" VARCHAR,'
        '"RightFirstTime" BOOLEAN,"ManufactureDate" DATE,"ReleaseDate" DATE)'
    )
    con.execute(
        """INSERT INTO "Batch" VALUES
        (1,1,1,90,100,'Released',TRUE ,DATE '2026-01-01',DATE '2026-01-11'),
        (2,1,1,80,100,'Released',FALSE,DATE '2026-01-01',DATE '2026-01-21'),
        (3,2,1,50,100,'Rejected',TRUE ,DATE '2026-01-01',DATE '2026-01-06')"""
    )
    con.execute(
        'CREATE TABLE "TestResult"("TestResultID" INT,"BatchID" INT,'
        '"ResultStatus" VARCHAR,"TestType" VARCHAR,"ResultValue" DOUBLE,'
        '"SpecificationMin" DOUBLE,"SpecificationMax" DOUBLE,'
        '"CalibrationCurrent" BOOLEAN)'
    )
    rows = []
    tid = 1
    for i in range(10):
        failed = i < 2
        value = (7.0 if i == 0 else 5.0) if failed else 10.0
        rows.append((tid, 1 if i < 5 else 2,
                     "Failed" if failed else "Pass", "Assay", value, 10.0, 20.0, True))
        tid += 1
    for _ in range(5):
        rows.append((tid, 3, "Pass", "Assay", 15.0, 10.0, 20.0, True))
        tid += 1
    con.executemany('INSERT INTO "TestResult" VALUES (?,?,?,?,?,?,?,?)', rows)
    con.close()
    return str(path)


#: Worked out by hand from the fixture above, not read back off a run.
EXPECTED: dict[str, dict[str, object]] = {
    "Batches Manufactured": {"North": 2, "South": 1},
    "Batches Released": {"North": 2, "South": 0},
    "Batches Rejected": {"North": 0, "South": 1},
    "Batch Rejection Rate": {"North": 0.0, "South": 1.0},
    "Batch Yield %": {"North": 0.85, "South": 0.50},
    "Right First Time %": {"North": 0.50, "South": 1.0},
    "Tests Performed": {"North": 10, "South": 5},
    "OOS Results": {"North": 2, "South": 0},
    "OOS Rate": {"North": 0.20, "South": 0.0},
    "Tests On Uncalibrated Instruments": {"North": 0, "South": 0},
    "Calibration Compliance %": {"North": 1.0, "South": 1.0},
    # 10 and 20 days -> 15; 5 days -> 5. Exercises the inlined calculated
    # column and DATE_DIFF together.
    "Average Days To Release": {"North": 15.0, "South": 5.0},
    # Failed values 7 and 5 against a spec minimum of 10 -> deviations 3 and 5.
    "Mean Deviation From Spec": {"North": 4.0, "South": None},
    # (7 + 5 + 8*10) / 10
    "Assay Mean": {"North": 9.2, "South": 15.0},
    "Quality Status": {"North": "Within expected range",
                       "South": "Above rejection threshold"},
}


@pytest.mark.parametrize("measure", sorted(EXPECTED))
def test_generated_sql_returns_the_hand_computed_answer(
    model, measures, warehouse, measure
):
    duckdb = pytest.importorskip("duckdb")
    result = translate(model, measures[measure], SITE)
    assert result.translated, f"{measure}: {result.reason}"

    con = duckdb.connect(warehouse, read_only=True)
    try:
        got = {row[0]: row[1] for row in con.execute(result.sql).fetchall()}
    finally:
        con.close()

    for site, expected in EXPECTED[measure].items():
        actual = got.get(site)
        if expected is None:
            assert actual is None, f"{measure} at {site}: expected NULL, got {actual}"
        elif isinstance(expected, str):
            assert actual == expected
        else:
            assert actual is not None, f"{measure} at {site}: got no row"
            assert abs(float(actual) - float(expected)) < 1e-9, (
                f"{measure} at {site}: expected {expected}, got {actual}"
            )


# -- the endpoint that serves the whole dataset --------------------------------

def _dataset(model, **params):
    from concordance.graph.csg import SemanticGraph
    from concordance.web import api

    registry = api.ModelRegistry.of(api.ApiContext(graph=SemanticGraph(model)))
    return api.handle(registry, "/api/dataset", params)


def test_the_dataset_endpoint_returns_every_measure(model):
    status, payload = _dataset(model, grain=["Site[SiteName]"])
    assert status == 200
    assert payload["counts"]["measures"] == len(model.measures)
    assert len(payload["measures"]) == len(model.measures)


def test_translated_and_blocked_counts_partition_the_measures(model):
    _, payload = _dataset(model, grain=["Site[SiteName]"])
    counts = payload["counts"]
    assert counts["translated"] + counts["blocked"] == counts["measures"]


def test_a_blocked_measure_carries_its_reason_not_an_empty_string(model):
    """The reason is the useful content for a measure with no SQL. A blank
    would leave a reader unable to tell "we cannot" from "we did not try"."""
    _, payload = _dataset(model, grain=["Site[SiteName]"])
    for row in payload["measures"]:
        if row["status"] != "exact":
            assert row["sql"] == ""
            assert row["reason"]
            assert row["blocked_by"]


def test_grain_options_offer_dimensions_not_facts(model):
    """Batch is pointed at by TestResult but also points at Product, Site and
    Calendar, which makes it a fact table. Grouping by one of its own numbers
    is legal SQL and never the question."""
    _, payload = _dataset(model)
    tables = {o["table"] for o in payload["grain_options"]}
    assert "Batch" not in tables
    assert {"Site", "Calendar", "Product"} <= tables


def test_a_join_key_is_not_offered_as_a_grain(model):
    """Grouping by an opaque id tells the reader nothing they came for."""
    _, payload = _dataset(model)
    assert "Site[SiteID]" not in {o["value"] for o in payload["grain_options"]}


def test_the_dialect_reaches_the_generated_sql(model):
    _, payload = _dataset(model, grain=["Site[SiteName]"], dialect=["snowflake"])
    assert payload["dialect"] == "snowflake"
    sql = " ".join(r["sql"] for r in payload["measures"] if r["sql"])
    assert "COUNT_IF" in sql


def test_an_unknown_dialect_is_refused_rather_than_ignored(model):
    """Silently serving DuckDB syntax to someone who asked for Snowflake would
    hand them a query that fails on their warehouse with no clue why."""
    status, payload = _dataset(model, dialect=["oracle"])
    assert status == 400
    assert "oracle" in payload["error"]


def test_no_grain_is_a_valid_request(model):
    """The whole-model figure is one row, not an error."""
    status, payload = _dataset(model)
    assert status == 200
    assert payload["grain"] == []
    served = [r for r in payload["measures"] if r["sql"]]
    assert served and all("GROUP BY" not in r["sql"] for r in served)


# -- the FRD carrying its own SQL ---------------------------------------------

def _frd(model, **kwargs):
    from concordance.generate.document import build
    from concordance.generate.requirements import Kind
    from concordance.graph.csg import SemanticGraph

    return build(SemanticGraph(model), Kind.FUNCTIONAL, generated_on="2026-01-01", **kwargs)


def test_the_frd_carries_sql_when_a_grain_is_given(model):
    from concordance.generate.document import to_markdown

    text = to_markdown(_frd(model, sql_grain=SITE))
    assert text.count("*Equivalent SQL*") == 16
    assert text.count("*No SQL equivalent:*") == 4


def test_the_sql_sits_with_the_requirement_not_in_an_appendix(model):
    """What makes the document work as retrieval input: a chunk that lands on
    a requirement carries its DAX and its SQL, rather than a query with nothing
    saying what it is for."""
    from concordance.generate.document import to_markdown

    text = to_markdown(_frd(model, sql_grain=SITE))
    body = text.split("## Traceability matrix")[0]
    assert body.count("*Equivalent SQL*") == 16, "SQL drifted out of the sections"

    start = body.index("### REQ-F-", body.index("Measure definitions"))
    block = body[start : body.index("### REQ-F-", start + 10)]
    assert "*Implementation:*" in block and "*Equivalent SQL*" in block


def test_the_grain_is_stated_in_the_front_matter(model):
    """SQL shown without the grain it was rendered at is a claim the document
    cannot support: the same measure at another grain is another query."""
    from concordance.generate.document import to_markdown

    text = to_markdown(_frd(model, sql_grain=SITE))
    assert "**SQL:** 16 of 20 measures" in text
    assert "Site[SiteName]" in text.split("## ")[0]


def test_a_measure_with_no_sql_says_why_in_the_document(model):
    from concordance.generate.document import to_markdown

    text = to_markdown(_frd(model, sql_grain=SITE))
    assert "PREVIOUSMONTH shifts the date filter context rather than reading it." in text
    assert "property of the expression rather than a gap in the translation" in text


def test_the_brd_never_carries_sql(model):
    """A BRD states what the business needs, not how a query would express it."""
    from concordance.generate.document import build
    from concordance.generate.requirements import Kind
    from concordance.graph.csg import SemanticGraph

    built = build(SemanticGraph(model), Kind.BUSINESS, sql_grain=SITE)
    assert built.sql == {}


def test_omitting_the_grain_leaves_the_document_exactly_as_it_was(model):
    """The feature is opt-in. Anyone generating an FRD the way they did last
    week gets the same bytes."""
    from concordance.generate.document import to_markdown

    assert "Equivalent SQL" not in to_markdown(_frd(model))
    assert "**SQL:**" not in to_markdown(_frd(model))


def test_the_dialect_reaches_the_document(model):
    from concordance.generate.document import to_markdown

    text = to_markdown(_frd(model, sql_grain=SITE, sql_dialect="snowflake"))
    assert "rendered as snowflake" in text
    assert "COUNT_IF" in text


def test_word_renders_sql_as_real_line_breaks(model):
    """Word does not break a run on a newline, so a query added as one run
    would render as a single unwrapped line running off the page."""
    docx = pytest.importorskip("docx")
    from concordance.generate.word import render

    import io

    buffer = io.BytesIO()
    render(_frd(model, sql_grain=SITE)).save(buffer)
    buffer.seek(0)
    paragraphs = docx.Document(buffer).paragraphs
    captions = [p for p in paragraphs if p.text.startswith("Equivalent SQL")]
    assert len(captions) == 16

    query = next(p for p in paragraphs if p.text.startswith("SELECT"))
    assert query._p.xml.count("<w:br/>") >= 3


# -- against Power BI files this project did not write -------------------------
#
# Everything above tests the three models authored for this project, which is
# a fair test of the compiler and no test at all of its coverage: they were
# written by the same people who wrote the translator, in the DAX it happens to
# read. A reviewer made exactly that point, and running the translator over
# Microsoft's own published samples proved it -- seventeen measures came back
# "not a function this translator reads yet", including seven for CONCATENATE
# and four for a plain parse failure on Power BI's automatic date hierarchies.
#
# These tests pin the distinction that survived. Coverage on somebody else's
# model is genuinely lower and that is honest; what must not happen is a refusal
# that blames the DAX for a gap in the reader.

REAL_MODELS = [
    Path("data/models/Supply_Chain_Sample.pbix"),
    Path("data/models/Sales_Returns_Sample.pbix"),
    Path("data/models/AdventureWorks_Sales.pbix"),
]


def _real(path: Path):
    if not path.exists():
        pytest.skip(f"sample not present: {path}")
    from concordance.adapters.pbix import PbixAdapter

    return PbixAdapter().extract(str(path))


@pytest.mark.parametrize("path", REAL_MODELS, ids=lambda p: p.stem)
def test_a_real_power_bi_file_leaves_no_translator_gap(path: Path) -> None:
    """Every refusal on a real model must be a fact about the DAX.

    UNSUPPORTED means "we have not built this yet" and BLOCKED means "this
    cannot be done". The two read completely differently to someone deciding
    whether to wait for a fix, so a gap dressed as a limit is a lie and a limit
    dressed as a gap is a promise that will never be kept.
    """
    gaps = [
        f"{t.measure}: {t.reason}"
        for t in translate_all(_real(path))
        if t.status is Status.UNSUPPORTED
    ]
    assert not gaps, "translator gaps on a real Power BI file:\n  " + "\n  ".join(gaps)


@pytest.mark.parametrize("path", REAL_MODELS, ids=lambda p: p.stem)
def test_every_query_from_a_real_model_is_valid_sql(path: Path) -> None:
    """Parsed with sqlglot rather than eyeballed, and in three dialects.

    A translator that emits confident nonsense is worse than one that refuses,
    so "it produced something" is not the assertion -- "a SQL engine agrees it
    is SQL" is.
    """
    import sqlglot

    for translation in translate_all(_real(path)):
        if translation.status is not Status.EXACT:
            continue
        sqlglot.parse_one(translation.sql, read="duckdb")
        for dialect in ("snowflake", "databricks"):
            assert to_dialect(translation.sql, dialect)


def test_the_hard_real_model_still_translates_something() -> None:
    """Sales & Returns is the honest hard case: 58 measures, most of them
    time-intelligence or ALL(), and six that come out exactly.

    Pinned so a change that quietly drops coverage to zero is caught. The
    number is low on purpose and is not a target to inflate -- the other test
    in this pair is what stops it being inflated dishonestly.
    """
    got = translate_all(_real(Path("data/models/Sales_Returns_Sample.pbix")))
    assert sum(1 for t in got if t.status is Status.EXACT) >= 6


def test_a_measure_can_be_referenced_with_its_table_in_front() -> None:
    """`'% Return Rate'[% Return Rate Value]` is a measure, written qualified.

    Valid DAX, and indistinguishable from a column reference until the column
    lookup misses. Two measures in a real sample were refused as "not a column
    in this model", which is wrong twice over: the thing exists, and the reader
    could have found it.
    """
    got = {t.measure: t for t in translate_all(_real(REAL_MODELS[1]))}
    for name in ("WIF Adjusted Units Returned", "WIF Adjusted Net Sales"):
        assert "is not a column in this model" not in got[name].reason


def test_an_automatic_date_hierarchy_parses_and_is_refused_for_a_real_reason() -> None:
    """`ALL('Calendar'[Date].[Month])` used to fail as "expected ')'".

    Power BI generates that hierarchy for every date column, so this is not an
    exotic corner -- and reporting a parse error made the model look malformed
    when the reader was simply not reading it.
    """
    got = {t.measure: t for t in translate_all(_real(REAL_MODELS[1]))}
    for name in ("WIF Sales", "Total Return Rate"):
        assert got[name].status is Status.BLOCKED
        assert "expected" not in got[name].reason
        assert "ALL" in got[name].reason


# -- the joins, as the dataset page and the FRD show them ----------------------

def test_a_join_is_built_from_the_same_code_as_the_queries(model) -> None:
    """The point of showing the join is that it is checkable against the query.

    If this section were formatted independently it could say one thing while
    the measures below it did another -- which is exactly the failure the whole
    project exists to catch, committed by the tool itself.
    """
    from concordance.generate.sql import joins

    on_page = {j.sql for j in joins(model)}
    in_query = {
        line.strip()
        for t in translate_all(model, SITE)
        if t.sql
        for line in t.sql.splitlines()
        if line.startswith("JOIN ")
    }
    for clause in in_query:
        assert any(clause in page for page in on_page), f"{clause} is not shown"


def test_inactive_relationships_are_listed_and_marked(model) -> None:
    """Hiding them would make a model look more connected than it is, and they
    are precisely what the confirmation queue exists to ask about."""
    from concordance.generate.sql import joins

    listed = joins(model)
    assert any(not j.active for j in listed)
    assert all(j.sql.startswith("FROM ") for j in listed)


@pytest.mark.parametrize("dialect", ["duckdb", "snowflake", "databricks"])
def test_a_join_is_quoted_for_the_dialect_it_is_shown_in(model, dialect: str) -> None:
    """Databricks quotes with backticks. A join rendered with the measures'
    dialect but not its quoting would be a query nobody could paste."""
    from concordance.generate.sql import joins

    sql = joins(model, dialect)[0].sql
    assert ("`" in sql) == (dialect == "databricks")


def test_the_frd_carries_the_join_sql_beneath_each_relationship() -> None:
    """Asked for so the document works as a RAG source: an agent given a query
    that joins two tables is stuck unless the same document says how they
    relate."""
    from concordance.generate import document

    from concordance.graph.csg import SemanticGraph

    graph = SemanticGraph(TmdlAdapter().extract(str(MODEL)))
    built = document.build(
        graph, document.Kind.FUNCTIONAL, sql_grain=(), sql_dialect="duckdb"
    )
    text = document.to_markdown(built)
    assert "*The same join in SQL*" in text
    assert 'FROM "Batch" JOIN "Site" ON "Batch"."SiteID" = "Site"."SiteID"' in text


def test_a_brd_carries_no_join_sql() -> None:
    """A BRD states what the business needs, not how a query expresses it."""
    from concordance.generate import document

    from concordance.graph.csg import SemanticGraph

    graph = SemanticGraph(TmdlAdapter().extract(str(MODEL)))
    built = document.build(graph, document.Kind.BUSINESS, sql_grain=())
    assert "*The same join in SQL*" not in document.to_markdown(built)

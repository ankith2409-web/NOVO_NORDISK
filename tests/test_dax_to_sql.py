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

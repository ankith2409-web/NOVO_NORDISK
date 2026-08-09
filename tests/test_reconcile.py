"""Comparing one KPI's Power BI definition against the warehouse's.

The QualityControl model and the DuckDB warehouse implement the same six QC
metrics twice, the way two teams would. Four are faithful re-implementations and
two diverge on purpose:

  * ``oos_rate`` divides by batches where the DAX measure divides by tests
  * ``batch_yield_pct`` averages per-batch ratios where the DAX measure takes
    the ratio of sums

Both must be found, and -- the part that is easy to get wrong -- the other four
must come back clean. A checker that flags everything is no more useful than one
that flags nothing.

Most of what follows are regression tests for bugs that made the first version
of this report worthless while looking like it worked.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import duckdb
import pytest

from concordance.adapters import sql as sqladapter
from concordance.adapters.tmdl import TmdlAdapter
from concordance.graph.csg import SemanticGraph
from concordance.reconcile import metrics
from concordance.reconcile.metrics import MetricDefinition, Verdict

QUALITY_CONTROL = Path("data/models/QualityControl.SemanticModel")
BUILDER = Path("scripts/build_warehouse.py")


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_warehouse", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def warehouse_path(tmp_path_factory) -> Path:
    """Built fresh, so the tests never depend on a database someone left behind."""
    return _load_builder().build(tmp_path_factory.mktemp("warehouse") / "qc.duckdb")


@pytest.fixture()
def warehouse(warehouse_path: Path):
    connection = duckdb.connect(str(warehouse_path), read_only=True)
    try:
        yield sqladapter.from_duckdb(connection)
    finally:
        connection.close()


@pytest.fixture(scope="session")
def power_bi() -> SemanticGraph:
    if not QUALITY_CONTROL.exists():
        pytest.skip(f"model not present: {QUALITY_CONTROL}")
    return SemanticGraph(TmdlAdapter().extract(str(QUALITY_CONTROL)))


@pytest.fixture()
def report(power_bi: SemanticGraph, warehouse):
    return metrics.reconcile(
        metrics.from_power_bi(power_bi), metrics.from_warehouse(warehouse)
    )


def _for(report, name: str):
    return next(c for c in report.comparisons if c.metric == name)


# -- the finding itself -------------------------------------------------------

def test_the_divergent_denominator_is_found(report) -> None:
    comparison = _for(report, "OOS Rate")
    assert comparison.verdict is Verdict.DIVERGENT

    # Found for the right reason: the warehouse reaches a table the measure
    # never touches. A verdict that happened to be right for a wrong reason
    # would break the moment the fixture changed.
    detail = " ".join(d.detail for d in comparison.differences if d.aspect == "source tables")
    assert "batch" in detail
    warehouse_side = next(d for d in comparison.definitions if d.language == "sql")
    power_bi_side = next(d for d in comparison.definitions if d.language == "dax")
    assert "batch" in {n.casefold() for n in warehouse_side.tables}
    assert "batch" not in {n.casefold() for n in power_bi_side.tables}


def test_the_average_of_ratios_is_found(report) -> None:
    """Same inputs, different aggregation -- real, but not provably wrong."""
    comparison = _for(report, "Batch Yield %")
    assert comparison.verdict is Verdict.REVIEW

    aspects = {d.aspect for d in comparison.differences}
    assert aspects == {"aggregation"}
    detail = next(d.detail for d in comparison.differences)
    assert "SUM" in detail and "AVG" in detail


@pytest.mark.parametrize(
    "name",
    ["Batches Manufactured", "Batches Rejected", "Tests Performed", "OOS Results"],
)
def test_faithful_reimplementations_are_not_flagged(report, name: str) -> None:
    assert _for(report, name).verdict is Verdict.CONSISTENT


def test_a_metric_only_the_warehouse_defines_is_not_a_conflict(report) -> None:
    assert "instrument_utilisation" in report.unique_to_platform["warehouse"]
    assert not any(c.metric == "instrument_utilisation" for c in report.comparisons)


def test_counts_add_up(report) -> None:
    counts = report.counts()
    assert counts["shared_metrics"] == len(report.comparisons)
    assert (
        counts["divergent"] + counts["review"] + counts["consistent"]
        == counts["shared_metrics"]
    )


# -- measure composition ------------------------------------------------------
# `OOS Rate` is DIVIDE([OOS Results], [Tests Performed], 0). It names no table
# and no column, so compared as written it reads nothing at all and would be
# flagged against any warehouse implementation whatsoever -- including a correct
# one. Resolving what its dependencies read is what makes the comparison mean
# anything.

def test_a_composed_measure_inherits_what_its_dependencies_read(power_bi) -> None:
    direct = next(m for m in power_bi.model.measures if m.name == "OOS Rate")
    assert not direct.depends_on_tables, "fixture no longer exercises composition"

    resolved = next(
        d for d in metrics.from_power_bi(power_bi) if d.name == "OOS Rate"
    )
    assert {t.casefold() for t in resolved.tables} == {"testresult"}
    assert {c.casefold() for c in resolved.columns} == {"resultstatus"}
    assert "COUNT" in resolved.aggregations


def test_the_report_says_which_measures_it_resolved_through(power_bi) -> None:
    """A table credited to a measure that never names it has to be explainable."""
    resolved = next(d for d in metrics.from_power_bi(power_bi) if d.name == "OOS Rate")
    assert resolved.resolved_through == ("OOS Results", "Tests Performed")


def test_composition_does_not_recurse_forever() -> None:
    """DAX rejects circular measures; a malformed export should not hang the run.

    Not reachable through either adapter, which is exactly why it is asserted
    here rather than left to chance.
    """
    def define(name: str, table: str) -> MetricDefinition:
        return MetricDefinition(
            name=name,
            platform="p",
            language="dax",
            expression="",
            tables=frozenset({table}),
        )

    direct = {"a": define("a", "ta"), "b": define("b", "tb")}
    cyclic = {"a": frozenset({"b"}), "b": frozenset({"a"})}

    resolved = {d.name: d for d in metrics._resolve(direct, cyclic)}
    assert resolved["a"].tables == frozenset({"ta", "tb"})


# -- name matching ------------------------------------------------------------

def test_percent_and_pct_are_the_same_metric() -> None:
    """SQL will not take a % in an identifier, so the warehouse spells it out.

    Dropping the symbol instead of expanding it left `Batch Yield %` unmatched,
    and a metric missing from the report is indistinguishable from one that
    agrees.
    """
    assert metrics.normalise_name("Batch Yield %") == metrics.normalise_name(
        "batch_yield_pct"
    )


@pytest.mark.parametrize(
    "left,right",
    [("OOS Rate", "oos_rate"), ("Right First Time %", "RIGHT-FIRST-TIME-PCT")],
)
def test_names_match_across_casing_and_separators(left: str, right: str) -> None:
    assert metrics.normalise_name(left) == metrics.normalise_name(right)


def _definition(name: str, platform: str, **kwargs) -> MetricDefinition:
    """A metric definition with only the fields a pairing decision reads."""
    return MetricDefinition(
        name=name,
        platform=platform,
        language="dax" if platform == "power_bi" else "sql",
        expression="",
        tables=frozenset(kwargs.get("tables", ())),
        columns=frozenset(kwargs.get("columns", ())),
        aggregations=frozenset(kwargs.get("aggregations", ())),
    )


def test_unmatched_lookalikes_are_offered_but_never_compared() -> None:
    pairings = metrics._possible_pairings(
        {
            "power_bi": [
                _definition("Average Days To Release", "power_bi"),
                _definition("Quality Status", "power_bi"),
            ],
            "warehouse": [_definition("avg_days_to_release", "warehouse")],
        }
    )
    assert len(pairings) == 1
    assert pairings[0].left == "Average Days To Release"
    assert pairings[0].right == "avg_days_to_release"
    assert pairings[0].similarity >= 0.80


def test_a_suggested_pairing_carries_no_verdict(report) -> None:
    """Suggestions are questions. None of them may reach the comparison list."""
    suggested = {p.left for p in report.possible_pairings} | {
        p.right for p in report.possible_pairings
    }
    compared = {c.metric for c in report.comparisons}
    assert not (suggested & compared)


# -- warehouse extraction -----------------------------------------------------
# Each of these locks down a bug that produced a plausible-looking report built
# on wrong data.

def test_engine_internal_views_do_not_become_metrics(warehouse) -> None:
    """DuckDB attaches a `system` catalog that also has a schema called `main`.

    Filtering on the schema alone pulled in fourteen internal views --
    `duckdb_columns`, `sqlite_master` and friends -- and every one arrived
    looking exactly like a user metric.
    """
    names = {m.name for m in warehouse.measures}
    assert not {n for n in names if n.startswith(("duckdb_", "sqlite_", "pragma_"))}
    assert len(warehouse.measures) == 7


def test_views_are_not_also_documented_as_stored_tables(warehouse) -> None:
    """`information_schema.tables` lists views too, by design of the standard."""
    stored = {t.name for t in warehouse.tables if not t.is_measure_only}
    assert stored == {"batch", "test_result", "product", "site"}
    assert not stored & {m.name for m in warehouse.measures}


def test_measures_have_a_container_to_hang_from(warehouse) -> None:
    containers = [t for t in warehouse.tables if t.is_measure_only]
    assert len(containers) == 1
    assert {m.table for m in warehouse.measures} == {containers[0].name}


def test_columns_belong_to_stored_tables_only(warehouse) -> None:
    stored = {t.name for t in warehouse.tables if not t.is_measure_only}
    assert {c.table for c in warehouse.columns} <= stored


# -- SQL analysis -------------------------------------------------------------

def test_a_view_does_not_count_as_reading_itself() -> None:
    """`view_definition` returns the whole CREATE statement on most engines.

    Left wrapped, the CREATE target parses as a source table, so every view
    reads a table no measure does -- and since a differing table set is what
    marks a metric divergent, every single comparison came back divergent.
    """
    tables, _ = sqladapter.references(
        "CREATE VIEW oos_rate AS SELECT COUNT(*) FROM test_result"
    )
    assert tables == frozenset({"test_result"})


def test_the_create_wrapper_is_optional() -> None:
    """Databricks returns the SELECT alone where DuckDB returns the statement."""
    wrapped = "CREATE VIEW v AS SELECT COUNT(*) AS n FROM batch"
    assert sqladapter.canonicalise(wrapped) == sqladapter.canonicalise(
        "SELECT COUNT(*) AS n FROM batch"
    )


def test_an_engine_rewritten_count_is_still_a_count() -> None:
    """DuckDB stores COUNT(*) as count_star(), which sqlglot leaves anonymous.

    COUNT is the aggregation that separates most warehouse metrics, so missing
    it made every count-based metric look like it aggregated nothing.
    """
    assert "COUNT_STAR" in sqladapter.aggregations(
        "CREATE VIEW v AS SELECT count_star() FROM batch"
    )
    definition = MetricDefinition(
        name="v",
        platform="warehouse",
        language="sql",
        expression="",
        aggregations=metrics._normalise_aggregations(frozenset({"COUNT_STAR"})),
    )
    assert definition.aggregations == frozenset({"COUNT"})


def test_reformatting_a_view_is_not_a_change() -> None:
    assert sqladapter.canonicalise(
        "select\n  count(*) as n\nfrom batch\nwhere status = 'Rejected'"
    ) == sqladapter.canonicalise(
        "SELECT COUNT(*) AS n FROM batch WHERE status = 'Rejected'"
    )


def test_renaming_a_view_is_not_a_change() -> None:
    """The fingerprint tracks the logic; identity is tracked by the name."""
    assert sqladapter.canonicalise(
        "CREATE VIEW oos_rate AS SELECT COUNT(*) FROM batch"
    ) == sqladapter.canonicalise("CREATE VIEW oos_ratio AS SELECT COUNT(*) FROM batch")


def test_a_changed_literal_is_a_change() -> None:
    """String literals are data, so their casing has to survive canonicalisation."""
    assert sqladapter.canonicalise(
        "SELECT COUNT(*) FROM batch WHERE status = 'Rejected'"
    ) != sqladapter.canonicalise(
        "SELECT COUNT(*) FROM batch WHERE status = 'rejected'"
    )


def test_unparseable_sql_degrades_instead_of_failing(warehouse_path: Path) -> None:
    garbage = "this is not sql at all((("
    assert sqladapter.canonicalise(garbage) == garbage
    assert sqladapter.references(garbage) == (frozenset(), frozenset())
    assert sqladapter.aggregations(garbage) == frozenset()


@pytest.mark.parametrize("bad", ["main'; DROP TABLE batch; --", "a b", "", "1x-y"])
def test_a_schema_name_that_is_not_an_identifier_is_refused(bad: str) -> None:
    """Interpolated identifiers are guarded rather than escaped and hoped for."""
    with pytest.raises(ValueError):
        sqladapter.SqlAdapter(connection=None, schema=bad)


# -- rendering ----------------------------------------------------------------

def test_the_text_report_shows_the_conflict(report) -> None:
    text = metrics.to_text(report)
    assert "OOS Rate" in text
    assert "Divergent" in text
    # The reader has to be able to see *why* without opening the model.
    assert "batch" in text
    assert "via OOS Results, Tests Performed" in text


# -- how a difference is worded ------------------------------------------------

def test_a_difference_reads_as_a_sentence_not_a_python_repr(report) -> None:
    """``power_bi reads ['batch', 'testresult']`` was reaching the interface,
    the generated document and the terminal alike -- an f-string interpolating
    a list. What is compared did not change; only how it is said."""
    comparison = _for(report, "OOS Rate")
    detail = next(d.detail for d in comparison.differences if d.aspect == "source tables")

    assert "[" not in detail and "'" not in detail
    assert "Power BI reads" in detail
    assert "the warehouse reads" in detail
    # Still names both tables, and still separates the two sides.
    assert "batch, testresult" in detail
    assert detail.count(";") == 1


# -- pairing on evidence, not just on spelling --------------------------------
# A name score cannot tell a synonym from an antonym: measured against this
# model, "Batches Released" and "batches_rejected" score 0.80 while the true
# pair "Right First Time %" / "right_first_time_rate" scores 0.86. These pin
# the evidence that settles such a case, and the pairs a name score would miss
# entirely.

def test_a_pair_no_name_measure_could_find_is_found_by_what_it_reads() -> None:
    """The capability names structurally cannot provide.

    "OOS Rate" against "quality_failure_ratio" scores far below any workable
    threshold -- lowering the cut far enough to catch it would flood the list
    with noise. Reading the same column and applying the same aggregation is
    what identifies it.
    """
    assert (
        metrics.SequenceMatcher(None, "oosrate", "qualityfailureratio").ratio() < 0.5
    ), "if these ever became name-similar this test would prove nothing"

    pairings = metrics._possible_pairings(
        {
            "power_bi": [
                _definition(
                    "OOS Rate",
                    "power_bi",
                    tables={"TestResult"},
                    columns={"ResultStatus"},
                    aggregations={"COUNT"},
                )
            ],
            "warehouse": [
                _definition(
                    "quality_failure_ratio",
                    "warehouse",
                    tables={"test_result"},
                    columns={"result_status"},
                    aggregations={"COUNT"},
                )
            ],
        }
    )
    assert len(pairings) == 1
    assert pairings[0].basis == "structure"
    assert pairings[0].shared_columns == ("resultstatus",)
    assert "both use" in pairings[0].evidence


def test_a_close_name_reading_nothing_in_common_is_marked_contradicted() -> None:
    """The "Released"/"rejected" shape. Still offered -- one side may simply be
    unparseable -- but never ranked as though the structure agreed."""
    pairings = metrics._possible_pairings(
        {
            "power_bi": [
                _definition(
                    "Batches Released",
                    "power_bi",
                    tables={"Batch"},
                    columns={"ReleaseDate"},
                    aggregations={"COUNT"},
                )
            ],
            "warehouse": [
                _definition(
                    "batches_restated",
                    "warehouse",
                    tables={"restatement"},
                    columns={"restated_on"},
                    aggregations={"SUM"},
                )
            ],
        }
    )
    assert len(pairings) == 1
    assert pairings[0].basis == "name"
    assert pairings[0].contradicted is True
    assert "nothing in common" in pairings[0].evidence


def test_agreeing_on_both_outranks_agreeing_on_either() -> None:
    """A reviewer should meet the pairs they can settle fastest first."""
    pairings = metrics._possible_pairings(
        {
            "power_bi": [
                _definition(
                    "Right First Time %",
                    "power_bi",
                    columns={"Status"},
                    aggregations={"COUNT"},
                ),
                _definition("Assay Mean", "power_bi", columns={"Assay"}),
            ],
            "warehouse": [
                _definition(
                    "right_first_time_rate",
                    "warehouse",
                    columns={"status"},
                    aggregations={"COUNT"},
                ),
                _definition("assay_max", "warehouse", columns={"potency"}),
            ],
        }
    )
    assert pairings[0].basis == "both"
    assert pairings[0].left == "Right First Time %"
    # The antonym pair is still offered, and still last.
    assert pairings[-1].contradicted is True


def test_a_shared_fact_table_alone_does_not_propose_a_pair() -> None:
    """Two unrelated metrics over one fact table share it trivially. Proposing
    on that alone would make every metric a candidate for every other."""
    pairings = metrics._possible_pairings(
        {
            "power_bi": [
                _definition("Headcount", "power_bi", tables={"Batch"}, columns={"Staff"})
            ],
            "warehouse": [
                _definition("shipment_weight", "warehouse", tables={"batch"}, columns={"kg"})
            ],
        }
    )
    assert pairings == []


def test_a_pairing_still_carries_no_verdict() -> None:
    """The whole contract. Structural agreement is evidence of a shared
    subject, never proof of a shared meaning, so nothing here may decide."""
    pairing = metrics._possible_pairings(
        {
            "power_bi": [
                _definition("OOS Rate", "power_bi", columns={"Status"}, aggregations={"COUNT"})
            ],
            "warehouse": [
                _definition("failure_ratio", "warehouse", columns={"status"}, aggregations={"COUNT"})
            ],
        }
    )[0]
    assert not hasattr(pairing, "verdict")


def test_a_shared_aggregation_over_a_shared_table_does_not_propose_a_pair() -> None:
    """Measured, not assumed. Allowing table+aggregation to propose produced six
    suggestions for one unmatched warehouse metric on the real QualityControl
    fixture, down to a name similarity of 0.06 -- because nearly every metric
    counts something over the same fact table.
    """
    pairings = metrics._possible_pairings(
        {
            "power_bi": [
                _definition(
                    "OOS Results PM",
                    "power_bi",
                    tables={"TestResult"},
                    columns={"ResultStatus"},
                    aggregations={"COUNT"},
                )
            ],
            "warehouse": [
                _definition(
                    "instrument_utilisation",
                    "warehouse",
                    tables={"test_result"},
                    columns={"instrument_id"},
                    aggregations={"COUNT"},
                )
            ],
        }
    )
    assert pairings == []


def test_the_real_warehouse_yields_one_useful_suggestion(report) -> None:
    """The end-to-end shape, against the real model and the real warehouse.

    `Instrument Failure Rank` and `instrument_utilisation` score 0.667 on name
    -- below the 0.80 cut, so name matching alone would never have offered them.
    Sharing the instrument column is what surfaces the question.
    """
    assert len(report.possible_pairings) == 1
    pairing = report.possible_pairings[0]
    assert pairing.similarity < metrics._PAIRING_THRESHOLD
    assert pairing.basis == "structure"
    assert "instrument" in pairing.shared_columns


# -- what happens when the database itself is the problem --------------------
# Found by hand while answering "what if the database changes": a truncated or
# corrupted --warehouse file reached the CLI and the server as a raw
# duckdb.IOException -- a stack trace for the CLI, a dropped connection with no
# body at all for the server. Neither is this input being wrong in an ordinary
# way; both looked like the tool itself crashing.

@pytest.fixture
def corrupt_warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "corrupt.duckdb"
    path.write_text("this is not a duckdb file", encoding="utf-8")
    return path


def test_the_cli_reports_a_corrupt_warehouse_without_a_traceback(
    corrupt_warehouse: Path, capsys
) -> None:
    from concordance.cli import main

    if not QUALITY_CONTROL.exists():
        pytest.skip(f"model not present: {QUALITY_CONTROL}")
    code = main([
        "reconcile", str(QUALITY_CONTROL), "--warehouse", str(corrupt_warehouse),
    ])
    assert code == 2
    assert "Cannot open the warehouse" in capsys.readouterr().err


def test_the_api_answers_with_a_gateway_error_not_a_dropped_connection(
    corrupt_warehouse: Path, power_bi: SemanticGraph
) -> None:
    from concordance.web.api import ApiContext, ApiError, reconcile

    context = ApiContext(graph=power_bi, warehouse=corrupt_warehouse)
    with pytest.raises(ApiError) as caught:
        reconcile(context, {})
    assert caught.value.status == 502
    assert str(corrupt_warehouse) in caught.value.message

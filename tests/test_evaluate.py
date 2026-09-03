"""Running a measure's SQL against the model's own rows.

This is the only place in the project where a number reaches the screen that
was not copied out of a file, so these tests are less about the plumbing than
about whether the figures are *correct*.

The load-bearing test is `test_the_figures_agree_with_each_other`. Any
translator can produce a query that runs; a query that runs and returns a wrong
number looks exactly like one that returns a right one. What catches that is
arithmetic the model itself asserts -- `Sales` is defined as regular plus
markdown, so if the three computed figures do not add up, the translation is
wrong no matter how healthy the query looked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.adapters.tmdl import TmdlAdapter
from concordance.generate.evaluate import evaluate

STORE = Path("data/models/StoreSales.pbix")
TMDL = Path("data/models/DiabetesCare.SemanticModel")


@pytest.fixture(scope="module")
def run():
    if not STORE.exists():
        pytest.skip(f"model not present: {STORE}")
    return evaluate(PbixAdapter().extract(str(STORE)))


def _value(run, name: str) -> float:
    found = run.by_name()[name]
    assert found.computed, f"{name} did not compute: {found.reason}"
    return found.value


# -- are the numbers right? ----------------------------------------------------


def test_the_figures_agree_with_each_other(run) -> None:
    """Arithmetic the model asserts about itself, checked against what ran.

    Each of these is a relationship the DAX states outright, so it holds for
    the real data or the translation is wrong. A query that runs cleanly and
    sums the wrong column passes every other test in this file and fails here.
    """
    # `Sales = [Regular Sales $] + [Markdown Sales $]`
    assert _value(run, "Sales") == pytest.approx(
        _value(run, "Regular Sales $") + _value(run, "Markdown Sales $")
    )
    # `Units = [Regular Sales #] + [Markdown Sales #]`
    assert _value(run, "Units") == pytest.approx(
        _value(run, "Regular Sales #") + _value(run, "Markdown Sales #")
    )
    # `Total Sales Var = [Sales TY] - [Sales LY]`
    assert _value(run, "Total Sales Var") == pytest.approx(
        _value(run, "Sales TY") - _value(run, "Sales LY")
    )
    # `Avg $/Unit TY = [Sales TY] / [Units TY]`
    assert _value(run, "Avg $/Unit TY") == pytest.approx(
        _value(run, "Sales TY") / _value(run, "Units TY")
    )


def test_two_measures_with_the_same_definition_return_the_same_figure(run) -> None:
    """`This Year Sales` is defined as `[Sales TY]`, and nothing else.

    They are separate measures with separate SQL, so agreeing is a property of
    the translation rather than of sharing a code path.
    """
    assert _value(run, "This Year Sales") == pytest.approx(_value(run, "Sales TY"))
    assert _value(run, "Last Year Sales") == pytest.approx(_value(run, "Sales LY"))


def test_a_count_is_a_whole_number(run) -> None:
    """104 stores, not 104.3. A count that comes back fractional is a join
    fanning out -- the same defect that once made a batch count read 10 for 2."""
    stores = _value(run, "Stores")
    assert stores == int(stores)
    assert stores > 0


# -- what it refuses to answer -------------------------------------------------


def test_a_measure_with_no_sql_gets_no_figure_and_says_why(run) -> None:
    """Roughly one in ten refuses to translate, and none of them get a zero.

    A stand-in zero is the specific failure this guards: it is a plausible
    number, it sorts and formats like a real one, and nothing on screen would
    distinguish it from a figure that was actually computed.
    """
    blocked = [v for v in run.values if not v.computed]
    assert blocked, "the sample has measures that cannot translate"
    for value in blocked:
        assert value.value is None, f"{value.measure} was given a stand-in figure"
        assert value.reason, f"{value.measure} has no figure and no reason"


def test_every_computed_figure_carries_the_query_that_made_it(run) -> None:
    """The number is checkable rather than trusted."""
    for value in run.computed:
        assert value.sql.upper().startswith("SELECT")
        assert value.table, f"{value.measure} does not say which table it is on"


def test_most_of_the_model_computes(run) -> None:
    assert run.available
    assert run.rows_loaded > 1_000_000, "the fact table's rows are actually loaded"
    assert len(run.computed) == 29
    assert len(run.values) == 32


# -- the two traps that produced confident wrong figures -----------------------


def test_the_figure_is_read_by_name_not_by_position() -> None:
    """A query at a grain selects the grain first, and the measure after it.

    Reading column zero returns the *month*. Twelve of Sales & Returns'
    measures reported `2019-01-01` as their value before this was fixed -- a
    date where a percentage belongs, indistinguishable on a card from a figure
    that was actually computed.
    """
    from concordance.generate.evaluate import _single

    got = _single(
        "Net Sales PM",
        "Sales",
        "SELECT ...",
        ["month", "Net Sales PM"],
        [("2019-01-01", 1234.5)],
    )
    assert got.value == 1234.5


def test_a_measure_meaningful_per_period_gets_no_whole_model_figure() -> None:
    """A previous-month measure returns a row per month, not a total.

    Putting the first month's figure on a card would be picking one arbitrarily
    and labelling it as the model's. It is refused with the reason, and the
    query is still shown -- it runs, it just does not answer this question.
    """
    from concordance.generate.evaluate import _single

    got = _single(
        "Net Sales PM",
        "Sales",
        "SELECT ...",
        ["month", "Net Sales PM"],
        [("2019-01-01", 1.0), ("2019-02-01", 2.0)],
    )
    assert got.value is None
    assert "one row at a time" in got.reason
    assert got.sql, "the query is still offered"


def test_a_non_numeric_result_is_refused_rather_than_forced() -> None:
    """`float()` on a datetime raises, and an unhandled raise took the endpoint
    down entirely -- every model's dashboard, not just the odd measure's."""
    from datetime import datetime

    from concordance.generate.evaluate import _single

    got = _single("Odd", "T", "SELECT ...", ["Odd"], [(datetime(2019, 1, 1),)])
    assert got.value is None
    assert "not a number" in got.reason

    # `bool` is an `int` in Python and would otherwise render as 1.
    flag = _single("Flag", "T", "SELECT ...", ["Flag"], [(True,)])
    assert flag.value is None


def test_an_ambiguous_result_is_refused_rather_than_guessed() -> None:
    """Several columns and none named for the measure: which is the figure
    cannot be settled, and picking one is how a grain ends up on a card."""
    from concordance.generate.evaluate import _single

    got = _single("Missing", "T", "SELECT ...", ["a", "b"], [(1.0, 2.0)])
    assert got.value is None
    assert "cannot be settled" in got.reason

    # One column and no name match is not ambiguous, so it resolves.
    lone = _single("Missing", "T", "SELECT ...", ["whatever"], [(7.0,)])
    assert lone.value == 7.0


def test_every_sample_evaluates_without_taking_the_endpoint_down() -> None:
    """The cross-model check that found the datetime crash in the first place."""
    for name in ("StoreSales", "Sales_Returns_Sample", "Supply_Chain_Sample"):
        path = Path(f"data/models/{name}.pbix")
        if not path.exists():
            continue
        result = evaluate(PbixAdapter().extract(str(path)))
        assert result.available, name
        for value in result.values:
            assert value.computed or value.reason, f"{name}: {value.measure} is silent"


# -- a source with no rows behind it -------------------------------------------


def test_a_schema_without_data_says_so_rather_than_showing_zeros() -> None:
    """A `.SemanticModel` folder is a schema; there is nothing to query.

    It would be easy to return an empty result here and let the interface
    render a row of zeroes, which reads as "this model measures nothing"
    rather than "this file does not carry the rows".
    """
    if not TMDL.exists():
        pytest.skip(f"model not present: {TMDL}")
    result = evaluate(TmdlAdapter().extract(str(TMDL)))

    assert result.available is False
    assert result.values == ()
    assert "does not" in result.reason or "without carrying" in result.reason
    assert ".pbix" in result.reason, "it says what would work instead"


def test_an_unreadable_file_costs_the_figures_and_nothing_else(tmp_path) -> None:
    """A model whose rows will not load is still a model.

    Raising here would take down every other page for a model that documents
    perfectly well, to say nothing of the upload path, where somebody else's
    file is being read for the first time.
    """
    from concordance.model import SemanticModel

    broken = SemanticModel(
        name="Broken", source_path=str(tmp_path / "nope.pbix"), source_type="pbix"
    )
    result = evaluate(broken)

    assert result.available is False
    assert result.reason
    assert result.values == ()

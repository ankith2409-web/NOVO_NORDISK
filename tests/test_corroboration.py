"""Whether anything in the file shows a metric actually being used.

A second axis, added because of a fair criticism of the first one: a measure
left behind by a developer is *stated by the model* exactly as clearly as the
company's headline KPI, so the BRD asserted both at HIGH confidence and gave a
reader no way to tell them apart.

The fix is not to lower confidence. Confidence records provenance -- did the
model state this, or did we infer it -- and downgrading a real, declared
measure would be a lie about provenance told in order to smuggle in a hint
about quality. So these tests are as much about what did *not* change as about
what did.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.generate.requirements import (
    Confidence,
    Corroboration,
    Kind,
    RequirementDeriver,
)
from concordance.graph.csg import SemanticGraph

SALES_RETURNS = Path("data/models/Sales_Returns_Sample.pbix")
TMDL = Path("data/models/DiabetesCare.SemanticModel")


@pytest.fixture(scope="module")
def metrics():
    if not SALES_RETURNS.exists():
        pytest.skip(f"model not present: {SALES_RETURNS}")
    graph = SemanticGraph(PbixAdapter().extract(str(SALES_RETURNS)))
    derived = RequirementDeriver(graph).derive()
    return [r for r in derived if r.kind is Kind.BUSINESS and r.category == "Metrics and KPIs"]


# -- the axis it is not ---------------------------------------------------------


def test_confidence_is_not_quietly_degraded_by_disuse(metrics) -> None:
    """The load-bearing test.

    An unused measure is still declared by the model, so the statement "the
    solution reports this metric" is still HIGH-confidence: that is where it
    came from. Marking it MEDIUM would misreport provenance in order to hint at
    quality, which is the failure this whole design avoids.
    """
    unused = [r for r in metrics if r.uncorroborated]
    assert unused, "expected this model to contain metrics nothing uses"
    assert all(r.confidence is Confidence.HIGH for r in unused)


def test_being_unused_is_not_the_same_as_needing_review(metrics) -> None:
    # `needs_review` is the low-confidence queue. Mixing an unused-but-declared
    # measure into it would bury the statements that genuinely were inferred.
    assert not any(r.needs_review for r in metrics if r.uncorroborated)


# -- the axis it is -------------------------------------------------------------


def test_every_metric_is_placed_on_the_use_axis(metrics) -> None:
    assert metrics
    for requirement in metrics:
        assert requirement.corroboration is not Corroboration.NOT_A_METRIC


def test_a_metric_on_a_report_tile_is_corroborated_by_it(metrics) -> None:
    shown = [r for r in metrics if r.corroboration is Corroboration.SHOWN_ON_REPORT]
    assert shown, "this report shows measures on tiles"
    # The strongest evidence the file carries needs no caveat.
    assert all(not r.caveat for r in shown)


def test_a_metric_another_measure_reads_is_corroborated_by_that(metrics) -> None:
    read = [r for r in metrics if r.corroboration is Corroboration.READ_BY_A_MEASURE]
    assert read
    assert all(not r.caveat for r in read)


def test_an_uncorroborated_metric_carries_the_words_that_explain_it(metrics) -> None:
    unused = [r for r in metrics if r.uncorroborated]
    for requirement in unused:
        assert requirement.caveat
        # Named as a question, never as a verdict: the measure may be new, or
        # read by something outside this file entirely.
        assert "may be new" in requirement.caveat
        assert "defect" not in requirement.caveat.casefold()


def test_the_three_states_partition_the_metrics(metrics) -> None:
    counted = sum(
        1
        for r in metrics
        if r.corroboration
        in (
            Corroboration.SHOWN_ON_REPORT,
            Corroboration.READ_BY_A_MEASURE,
            Corroboration.NOTHING_IN_THIS_FILE,
        )
    )
    assert counted == len(metrics)


# -- a source with no report ----------------------------------------------------


def test_a_model_without_a_report_says_so_rather_than_assuming_disuse() -> None:
    """A `.SemanticModel` folder carries no report layer at all.

    The absence of a report is not evidence that nothing displays a measure, so
    the caveat has to say which of the two checks it was actually able to make.
    Claiming "no report tile shows it" about a source that has no tiles would be
    true and misleading.
    """
    if not TMDL.exists():
        pytest.skip(f"model not present: {TMDL}")
    from concordance.adapters.tmdl import TmdlAdapter

    graph = SemanticGraph(TmdlAdapter().extract(str(TMDL)))
    metrics = [
        r
        for r in RequirementDeriver(graph).derive()
        if r.kind is Kind.BUSINESS and r.category == "Metrics and KPIs"
    ]
    unused = [r for r in metrics if r.uncorroborated]
    if not unused:
        pytest.skip("every measure in this model is read by another")
    assert all("carries no report layer" in r.caveat for r in unused)


# -- the document says it -------------------------------------------------------


def test_the_brd_counts_them_and_marks_each_one() -> None:
    if not SALES_RETURNS.exists():
        pytest.skip(f"model not present: {SALES_RETURNS}")
    from concordance.generate import document

    graph = SemanticGraph(PbixAdapter().extract(str(SALES_RETURNS)))
    built = document.build(graph, document.Kind.BUSINESS)
    assert built.counts()["uncorroborated"] > 0

    text = document.to_markdown(built)
    assert "**In use:**" in text
    assert "*In use:*" in text
    # And the blind spot itself is stated, not left for the reader to infer.
    assert "Confidence records where a statement came from" in text


def test_the_brd_names_the_two_things_a_person_still_has_to_supply() -> None:
    if not SALES_RETURNS.exists():
        pytest.skip(f"model not present: {SALES_RETURNS}")
    from concordance.generate import document

    graph = SemanticGraph(PbixAdapter().extract(str(SALES_RETURNS)))
    text = document.to_markdown(document.build(graph, document.Kind.BUSINESS))
    assert "Which audience each metric is for" in text
    assert "Why each metric matters" in text
    # Neither is invented -- both are listed as owed by a person.
    assert "cannot be derived from the model" in text

"""Functional requirement wording: varied, and still true.

The old sentence was one fixed frame -- "**X** shall be implemented exactly as
the DAX expression recorded in the evidence for this requirement" -- identical
for all 58 measure requirements on Sales_Returns_Sample. A specification
nobody reads past page three is a specification nobody checks, which is the
failure this project exists to prevent.

Variety is the easy half and the least important. These tests are mostly about
the hard half: a sentence that says more can say something *false*, and the
first version of this did. `DIVIDE([Diff], CALCULATE(DISTINCTCOUNT(...)), 0)`
came out as "shall count the distinct values of Calendar[Date]" -- a ratio
described as a count, because an aggregation buried in the denominator was
read as the whole measure.
"""

from __future__ import annotations

import re
from collections import Counter

from concordance.adapters.pbix import PbixAdapter
from concordance.generate import patterns, phrasing
from concordance.generate.requirements import RequirementDeriver
from concordance.graph.csg import SemanticGraph


def say(expression: str, columns=(), upstream=()) -> str:
    return phrasing.functional_statement(
        "T[M]",
        patterns.detect(expression),
        patterns.aggregation_of(expression),
        list(columns),
        list(upstream),
    )


# -- the correctness half -----------------------------------------------------

def test_an_aggregation_inside_a_ratio_is_not_reported_as_the_measure() -> None:
    """The bug this module was rewritten to fix, pinned to the real expression
    it was found on."""
    statement = say(
        "DIVIDE([Diff], CALCULATE(DISTINCTCOUNT('Calendar'[Date]), ALL('Calendar')), 0)",
        columns=["Calendar[Date]"],
        upstream=["Diff"],
    )
    assert "ratio" in statement
    assert "shall count the distinct values" not in statement


def test_a_plain_aggregation_is_named_outright() -> None:
    assert "shall report the total of Sales[Amount]" in say(
        "SUM(Sales[Amount])", columns=["Sales[Amount]"]
    )
    assert "shall count the rows of" in say("COUNTROWS(Sales)", columns=["Sales[Id]"])


def test_a_time_intelligence_measure_leads_with_that() -> None:
    statement = say(
        "CALCULATE(SUM(Sales[Amount]), PREVIOUSMONTH('Calendar'[Date]))",
        columns=["Sales[Amount]"],
    )
    assert "shifted or cumulative date range" in statement
    # SUM is present, but the measure is a period comparison, not a total.
    assert "shall report the total of" not in statement


def test_every_statement_still_binds_to_the_evidence() -> None:
    """The one clause that must never vary: it is what ties the sentence to the
    fingerprint the requirement is bound to."""
    for expression in (
        "SUM(Sales[Amount])",
        "DIVIDE([A], [B])",
        "RANKX(ALL(Product), [Sales])",
        "IF([A] > 0, 1, 0)",
        "SomethingUnrecognised()",
        "",
    ):
        assert "recorded in the evidence for this requirement" in say(expression)


def test_an_unrecognised_expression_falls_back_rather_than_inventing() -> None:
    assert say("Mystery.Thing()") == (
        "**T[M]** shall be implemented, exactly as the DAX expression recorded "
        "in the evidence for this requirement."
    )


def test_secondary_behaviours_are_capped_not_dumped() -> None:
    """A deeply nested expression must not produce a sentence longer than the
    DAX it describes -- the expression itself is in the evidence."""
    many = patterns.detect(
        "RANKX(ALL(P), DIVIDE(CALCULATE(SUMX(P, [x]), USERELATIONSHIP(a, b)), "
        "DISTINCTCOUNT(P[k])))"
    )
    assert len(many) > 3
    note = phrasing.secondary_note(many)
    assert note.count(",") + 1 <= 2 or " and " in note
    assert len(note) < 120


def test_one_behaviour_produces_no_secondary_note() -> None:
    assert phrasing.secondary_note(patterns.detect("SUM(Sales[Amount])")) == ""


# -- determinism, which variety must not cost ---------------------------------

def test_the_same_expression_always_yields_the_same_sentence() -> None:
    expression = "DIVIDE(CALCULATE([A], ALL(T)), [B], 0)"
    first = say(expression, columns=["T[c]"], upstream=["A", "B"])
    for _ in range(20):
        assert say(expression, columns=["T[c]"], upstream=["A", "B"]) == first


# -- and the variety itself, measured on a real model -------------------------

def _frames(statements) -> Counter:
    """Sentences with every name removed, so only the frame is compared."""
    return Counter(
        re.sub(r"\[[^\]]*\]", "N", re.sub(r"\*\*.*?\*\*", "N", s)) for s in statements
    )


def test_a_real_model_no_longer_reads_the_same_way_every_time() -> None:
    graph = SemanticGraph(PbixAdapter().extract("data/models/Sales_Returns_Sample.pbix"))
    statements = [
        r.statement
        for r in RequirementDeriver(graph).derive()
        if r.category == "Measure definitions"
    ]
    assert len(statements) == 58, "the model this was measured on"

    frames = _frames(statements)
    # Was 4, and three of those differed only by a trailing dependency list --
    # every one of the 58 opened with the identical clause.
    assert len(frames) >= 15
    assert frames.most_common(1)[0][1] <= len(statements) // 3

"""Turning what was detected about a measure into a sentence that says it.

Every functional requirement about a measure used to read the same way:
"**X** shall be implemented exactly as the DAX expression recorded in the
evidence for this requirement." True, binding, and identical fifty-eight times
in a row on a real model -- which is a document nobody reads past the third
page, and a reviewer who skims a specification is the failure this project is
trying to prevent.

The fix is not to write more freely. It is to *say more*, out of facts already
established: which analytical behaviour the DAX exhibits (``patterns.detect``),
which single aggregation it applies if any (``patterns.aggregation_of``), and
what it reads. Those are facts about the DAX language applied to a real
expression, not judgements, so the sentence they produce is as derived and as
reproducible as the old one -- the same model yields the same document, byte
for byte, on every run.

Deliberately no language model. A generated paraphrase would vary between runs
and could not be traced to the fingerprint the requirement is bound to, which
would trade the one property that makes this document worth trusting for
prose that merely reads better.
"""

from __future__ import annotations

from concordance.generate.patterns import Pattern

#: Aggregation function -> the verb phrase that states what it does to a
#: column. Keyed on the function actually found in the expression, so a measure
#: described as totalling something is one whose DAX really calls SUM.
_AGGREGATION_VERBS: dict[str, str] = {
    "SUM": "shall report the total of",
    "AVERAGE": "shall report the mean of",
    "MEDIAN": "shall report the median of",
    "MIN": "shall report the lowest value of",
    "MAX": "shall report the highest value of",
    "COUNT": "shall count the numeric values in",
    "COUNTA": "shall count the non-blank values in",
    "COUNTROWS": "shall count the rows of",
    "DISTINCTCOUNT": "shall count the distinct values of",
    "DISTINCTCOUNTNOBLANK": "shall count the distinct non-blank values of",
    "SUMX": "shall total, row by row,",
    "AVERAGEX": "shall average, row by row,",
    "COUNTX": "shall count, row by row,",
    "MINX": "shall find the lowest value, row by row, over",
    "MAXX": "shall find the highest value, row by row, over",
    "PRODUCTX": "shall multiply, row by row,",
}

#: Pattern key -> a clause stating what the measure must do. Written to stand
#: on its own after the measure's name, and to be true of *every* expression
#: the pattern matches -- nothing here claims more than the presence of those
#: functions actually establishes.
_BEHAVIOUR_CLAUSES: dict[str, str] = {
    "time_intelligence": (
        "shall evaluate its result over the shifted or cumulative date range its "
        "expression defines, yielding a figure comparable with an earlier period "
        "or accumulated to date"
    ),
    "ranking": (
        "shall order members by value to produce the ranking its expression "
        "defines"
    ),
    "ratio": (
        "shall be computed as a ratio, yielding the fallback its expression "
        "specifies rather than an error when the denominator is zero or blank"
    ),
    "context_removal": (
        "shall evaluate with part of the active filter context deliberately "
        "removed, so the figure is relative to a wider total rather than to the "
        "current selection alone"
    ),
    "row_iteration": (
        "shall evaluate its expression once per row and aggregate those results, "
        "rather than aggregating a pre-computed column"
    ),
    "relationship_override": (
        "shall evaluate through a relationship other than the active one, so the "
        "same facts are analysed along a different key"
    ),
    "distinct_count": "shall count unique members rather than rows",
    "conditional": (
        "shall return whichever result the conditions in its expression select"
    ),
    "scope_aware": (
        "shall change what it reports according to the level of detail being "
        "viewed"
    ),
    "aggregation": "shall summarise its source column across the current filter context",
}


def _reading(columns: list[str], upstream: list[str]) -> str:
    """What the measure reads, as a phrase, or empty when nothing resolved."""
    if columns:
        return _join(columns)
    if upstream:
        return _join(f"[{name}]" for name in upstream)
    return ""


#: The patterns for which naming the aggregation *is* naming what the measure
#: does. Outside these, an aggregation found in the expression is an ingredient
#: of something larger, and leading with it states something false: on a real
#: model `DIVIDE([Diff], CALCULATE(DISTINCTCOUNT(Calendar[Date]), ALL(...)), 0)`
#: was described as "shall count the distinct values of Calendar[Date]" -- a
#: ratio reported as a count, because the denominator's function was read as
#: the whole measure.
_AGGREGATION_LED = frozenset({"aggregation", "distinct_count", "row_iteration"})


def functional_statement(
    qualified_name: str,
    detected: tuple[Pattern, ...],
    aggregation: str | None,
    columns: list[str],
    upstream: list[str],
) -> str:
    """The functional requirement's sentence for one measure.

    Layered so the most specific *true* thing leads -- and the ordering
    matters more than it looks. `patterns.detect` returns behaviours ranked by
    how much each says about intent, so its first entry is what the measure is
    for. Naming an aggregation is only allowed to pre-empt that when the
    aggregation is itself the leading behaviour; otherwise the sentence would
    describe an ingredient as though it were the dish.
    """
    source = _reading(columns, upstream)
    primary = detected[0].key if detected else ""
    verb = (
        _AGGREGATION_VERBS.get(aggregation or "")
        if primary in _AGGREGATION_LED
        else None
    )

    if verb and source:
        action = f"{verb} {source}"
    elif detected and detected[0].key in _BEHAVIOUR_CLAUSES:
        clause = _BEHAVIOUR_CLAUSES[detected[0].key]
        # "reading", not "computed from": several of the clauses above already
        # contain "computed", and the pair reads as a stutter.
        action = f"{clause}, reading {source}" if source else clause
    elif source:
        action = f"shall be computed from {source}"
    else:
        action = "shall be implemented"

    # The binding clause is the one part that does not vary, and should not:
    # it is the contract that ties the sentence to the fingerprint, and a
    # specification restating its own authority differently each time would be
    # worse writing, not better.
    return (
        f"**{qualified_name}** {action}, exactly as the DAX expression recorded "
        f"in the evidence for this requirement."
    )


def secondary_note(detected: tuple[Pattern, ...]) -> str:
    """The behaviours beyond the primary one, when the DAX shows more than one.

    A measure that both ranks and removes filter context is doing two things a
    reviewer should know about, and the leading clause can only name one.
    Capped at two so a heavily-nested expression does not produce a sentence
    longer than the DAX it describes -- the expression itself is in the
    evidence for anyone who needs all of it.
    """
    if len(detected) < 2:
        return ""
    extra = [pattern.label for pattern in detected[1:3]]
    return f" It also applies {_join(extra)}."


def _join(items) -> str:
    """`a`, `a and b`, `a, b and c` -- never a bare Python list repr."""
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"

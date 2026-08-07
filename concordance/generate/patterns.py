"""Recognising business intent in DAX.

A measure's function calls say a great deal about what it is *for*: an
expression using PREVIOUSMONTH is a period-over-period comparison, one using
RANKX produces a league table, one using DIVIDE reports a ratio. Naming those
patterns turns a wall of DAX into requirement language a business reader can
check.

This is deliberately rule-based rather than model-generated. The mapping from
function to meaning is a fact about the DAX language, not a judgement call, so
it should be deterministic, reviewable, and identical on every run.
"""

from __future__ import annotations

from dataclasses import dataclass

from concordance.normalize.dax import extract_functions


@dataclass(frozen=True)
class Pattern:
    """One recognised analytical behaviour."""

    key: str
    #: Business-facing name, used in requirement statements.
    label: str
    #: What the reader should understand the measure to be doing.
    description: str


#: Function name -> pattern. Only functions whose presence genuinely implies the
#: behaviour are listed; ambiguous ones (IF, SWITCH) are handled separately.
_TIME_INTELLIGENCE = {
    "PREVIOUSMONTH", "PREVIOUSYEAR", "PREVIOUSQUARTER", "PREVIOUSDAY",
    "SAMEPERIODLASTYEAR", "DATEADD", "PARALLELPERIOD", "TOTALYTD", "TOTALQTD",
    "TOTALMTD", "DATESYTD", "DATESQTD", "DATESMTD", "DATESBETWEEN",
    "DATESINPERIOD", "NEXTMONTH", "NEXTYEAR", "ENDOFMONTH", "STARTOFMONTH",
}
_RANKING = {"RANKX", "TOPN", "RANK"}
_RATIO = {"DIVIDE"}
_CONTEXT_REMOVAL = {"ALL", "ALLEXCEPT", "ALLSELECTED", "REMOVEFILTERS"}
_ROW_ITERATION = {"SUMX", "AVERAGEX", "COUNTX", "MINX", "MAXX", "PRODUCTX", "RANKX"}
_RELATIONSHIP_OVERRIDE = {"USERELATIONSHIP", "CROSSFILTER"}
_DISTINCT_COUNT = {"DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK"}
_CONDITIONAL = {"IF", "SWITCH", "IFERROR"}
_SCOPE_AWARE = {"ISINSCOPE", "HASONEVALUE", "SELECTEDVALUE", "ISFILTERED"}
_AGGREGATION = {
    "SUM", "AVERAGE", "COUNT", "COUNTA", "COUNTROWS", "MIN", "MAX", "MEDIAN",
}

PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "time_intelligence",
        "period-over-period comparison",
        "evaluates the metric over a shifted or cumulative date range, so results "
        "can be compared against a previous period or accumulated to date",
    ),
    Pattern(
        "ranking",
        "ranking",
        "orders members by value to produce a league table or top-N selection",
    ),
    Pattern(
        "ratio",
        "ratio",
        "divides one quantity by another, with explicit handling of division by zero",
    ),
    Pattern(
        "context_removal",
        "total-relative calculation",
        "deliberately ignores part of the active filter context, typically to "
        "compare a member against an overall total",
    ),
    Pattern(
        "row_iteration",
        "row-level calculation",
        "computes a value per row before aggregating, rather than aggregating "
        "pre-computed column values",
    ),
    Pattern(
        "relationship_override",
        "alternate relationship",
        "evaluates against a relationship other than the active one, allowing the "
        "same fact to be analysed along a different key",
    ),
    Pattern(
        "distinct_count",
        "distinct count",
        "counts unique members rather than rows",
    ),
    Pattern(
        "conditional",
        "conditional logic",
        "returns different results depending on business conditions",
    ),
    Pattern(
        "scope_aware",
        "context-sensitive behaviour",
        "changes what it reports depending on the level of detail being viewed",
    ),
    Pattern(
        "aggregation",
        "aggregation",
        "summarises a column across the rows in the current filter context",
    ),
)

_BY_KEY = {p.key: p for p in PATTERNS}

_RULES: tuple[tuple[str, set[str]], ...] = (
    ("time_intelligence", _TIME_INTELLIGENCE),
    ("ranking", _RANKING),
    ("ratio", _RATIO),
    ("relationship_override", _RELATIONSHIP_OVERRIDE),
    ("distinct_count", _DISTINCT_COUNT),
    ("context_removal", _CONTEXT_REMOVAL),
    ("row_iteration", _ROW_ITERATION),
    ("scope_aware", _SCOPE_AWARE),
    ("conditional", _CONDITIONAL),
    ("aggregation", _AGGREGATION),
)


def detect(expression: str) -> tuple[Pattern, ...]:
    """Identify the analytical behaviours an expression exhibits.

    Ordered by how much each says about business intent -- a measure that both
    ranks and aggregates is better described as a ranking than as a sum.
    """
    functions = extract_functions(expression)
    found: list[Pattern] = []
    for key, triggers in _RULES:
        if functions & triggers:
            found.append(_BY_KEY[key])
    return tuple(found)


def primary(expression: str) -> Pattern | None:
    """The single most descriptive behaviour, or None if nothing is recognised."""
    detected = detect(expression)
    return detected[0] if detected else None


def aggregation_of(expression: str) -> str | None:
    """Name the aggregation a measure applies, when it uses exactly one."""
    functions = extract_functions(expression)
    used = sorted(functions & (_AGGREGATION | _ROW_ITERATION | _DISTINCT_COUNT))
    return used[0] if len(used) == 1 else None

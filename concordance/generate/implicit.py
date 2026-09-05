"""Arithmetic a report does in its visuals rather than in a measure.

Power BI lets a report author drop a bare column onto a chart and pick an
aggregation on the tile -- `Sum of Sales Amount` -- instead of writing a
measure for it. Nothing about that is unusual; whole reports are built this
way. Microsoft's own AdventureWorks sample is one: it carries exactly *one*
measure, which uses `USERELATIONSHIP` and cannot be compiled, while every
figure a reader actually sees on its three pages is an implicit `Sum` declared
on a visual.

Before this module a model like that reached the dashboard with nothing to
show, and the page said so honestly and uselessly. The arithmetic was right
there in the file; it simply was not in `model.measures`, which is the only
place anything looked.

Two rules make this safe to do.

**These are not model objects, and they never join `model.measures`.** They are
derived on demand and handed to the query layer alone. Merging them in would
inflate the measure count, put invented objects into the BRD and the FRD, and
change the drift fingerprint of a model nobody edited -- which would be a
fabrication of exactly the kind this project exists to catch. Everything that
surfaces one labels it as the report's own aggregation.

**Nothing is guessed.** The table, the column and the aggregation are all
stated on the tile; this only rewrites them into the DAX that says the same
thing, so the existing translator can compile it. An aggregation the
translator has no equivalent for is dropped rather than approximated, and a
field naming a column the model does not contain is dropped too -- that is
already reported elsewhere as a coverage gap, and inventing a query over a
column that is not there would turn a known gap into a wrong number.
"""

from __future__ import annotations

import hashlib

from concordance.generate.breakdown import _named_like_an_id
from concordance.model import Measure

#: Power BI's tile aggregations, and the DAX that means the same thing. Only
#: the ones with an exact equivalent: `Median` and the percentiles have no
#: single-argument DAX form that compiles the same way, so a tile using one is
#: left out rather than quietly turned into something else.
AGGREGATIONS: dict[str, str] = {
    "sum": "SUM",
    "average": "AVERAGE",
    "avg": "AVERAGE",
    "min": "MIN",
    "max": "MAX",
    "count": "COUNT",
    "countnonnull": "COUNT",
    "distinctcount": "DISTINCTCOUNT",
    "countdistinct": "DISTINCTCOUNT",
}

#: Aggregations that only mean something over a number. `Sum` and `Average`
#: obviously; `Min` and `Max` less so, and that is the trap: Power BI's default
#: aggregation for a *text* column dropped on a tile is `Min`, which is how it
#: says "just show the label". Taken at face value it produces "Min of Store"
#: and "Min of District" -- alphabetically-first strings, offered on a
#: dashboard as though they were metrics. Counting is different: the distinct
#: count of a customer name is a real figure, so counts are allowed anywhere.
NUMERIC_ONLY = {"SUM", "AVERAGE", "MIN", "MAX"}

#: What a column's declared type has to contain to be worth summing.
_NUMERIC_TYPES = (
    "int", "double", "decimal", "currency", "number", "float", "money", "real",
)

#: How each aggregation is written out. Off the DAX rather than off the tile's
#: own spelling, because Power BI's internal names are not words -- a tile
#: saying `CountNonNull` would otherwise be offered as "Countnonnull of
#: Product ID".
_WORDS = {
    "SUM": "Sum",
    "AVERAGE": "Average",
    "MIN": "Min",
    "MAX": "Max",
    "COUNT": "Count",
    "DISTINCTCOUNT": "Distinct count",
}


def _is_numeric(model, table: str, column: str) -> bool:
    found = next(
        (
            c
            for c in model.columns
            if c.table.casefold() == table.casefold()
            and c.name.casefold() == column.casefold()
        ),
        None,
    )
    if found is None:
        return False
    declared = (found.data_type or "").casefold()
    return any(kind in declared for kind in _NUMERIC_TYPES)


def title(dax: str, column: str) -> str:
    """How one reads in a list beside the model's own measures."""
    return f"{_WORDS.get(dax, dax.title())} of {column}"


def _fingerprint(table: str, column: str, aggregation: str) -> str:
    """Stable across runs, and distinct from any real measure's.

    Prefixed, because a fingerprint is how this project decides two things are
    the same thing across two versions of a file. One of these colliding with a
    real measure's would let an invented object be accepted in place of an
    authored one.
    """
    seed = f"implicit:{table}[{column}]:{aggregation}".encode()
    return "implicit-" + hashlib.sha256(seed).hexdigest()[:16]


def from_report(model) -> list[Measure]:
    """Every aggregation the report declares on a visual, as a measure.

    Deduplicated: the same `Sum of Sales Amount` dropped on four tiles is one
    calculation, and offering it four times would say the report has four
    figures where it has one.
    """
    columns = {(c.table.casefold(), c.name.casefold()) for c in model.columns}
    known = {m.name.casefold() for m in model.measures}

    # Two tables can both carry an `Amount`, and "Sum of Amount" twice in one
    # list names two different calculations identically -- which is the exact
    # ambiguity this project exists to remove. Counted first so the name can
    # carry the table only where it has to.
    seen: dict[str, set[str]] = {}
    for page in getattr(model, "report_pages", ()) or ():
        for visual in getattr(page, "visuals", ()) or ():
            for field in getattr(visual, "fields", ()) or ():
                if field.aggregation and field.name:
                    seen.setdefault(field.name.casefold(), set()).add(field.table)
    shared = {name for name, tables in seen.items() if len(tables) > 1}

    found: dict[tuple[str, str, str], Measure] = {}
    for page in getattr(model, "report_pages", ()) or ():
        for visual in getattr(page, "visuals", ()) or ():
            for field in getattr(visual, "fields", ()) or ():
                aggregation = (field.aggregation or "").strip()
                dax = AGGREGATIONS.get(aggregation.casefold())
                if not dax or not field.table or not field.name:
                    continue
                if (field.table.casefold(), field.name.casefold()) not in columns:
                    # Bound to a field the extracted model does not carry.
                    # Reported as a coverage gap already; querying it would
                    # turn a known gap into a wrong answer.
                    continue
                if dax in NUMERIC_ONLY and not _is_numeric(model, field.table, field.name):
                    continue
                if dax in {"SUM", "AVERAGE"} and _named_like_an_id(field.name):
                    # A key is numeric and summing it is arithmetic on
                    # identifiers -- "Sum of ProductID" is a number with no
                    # referent. The same rule keeps keys off the charts.
                    continue
                key = (field.table, field.name, dax)
                if key in found:
                    continue
                name = title(
                    dax,
                    f"{field.table}[{field.name}]"
                    if field.name.casefold() in shared
                    else field.name,
                )
                if name.casefold() in known:
                    # An author who wrote a real measure by this name wins; two
                    # different things under one name is the ambiguity this
                    # whole project is against.
                    continue
                found[key] = Measure(
                    table=field.table,
                    name=name,
                    expression=f"{dax}({field.table}[{field.name}])",
                    fingerprint=_fingerprint(field.table, field.name, dax),
                    description=(
                        "Declared on a report visual rather than written as a measure. "
                        "The table, column and aggregation are the tile's own; this is "
                        "them rewritten as DAX so the figure can be computed and checked."
                    ),
                    depends_on_columns=frozenset({(field.table, field.name)}),
                )
    return list(found.values())


def is_implicit(measure: Measure) -> bool:
    """Whether a measure came from a visual rather than from the model."""
    return measure.fingerprint.startswith("implicit-")


def find(model, name: str) -> Measure | None:
    """One measure by name, the model's own first, then the report's.

    The model's own always wins, so nothing derived here can shadow something
    an author actually wrote.
    """
    for measure in model.measures:
        if measure.name == name:
            return measure
    for measure in from_report(model):
        if measure.name == name:
            return measure
    return None


def all_measures(model) -> list[Measure]:
    """The model's measures, then the report's implicit ones."""
    return list(model.measures) + from_report(model)

"""The filters a report applies before any of its numbers are computed.

This module exists because of a question that has an alarming shape: *why does
Power BI say 387.1K and Concordance say 1.2M for the same measure on the same
file?*

Neither was wrong. Microsoft's Sales & Returns report carries a report-level
filter pinning the whole report to **June**, and the card shows `Net Sales`
inside that filter. Concordance was reporting the same measure with no filter
at all -- every month in the file. Both figures are correct answers to
different questions, and until this module existed the tool showed one of them
while giving the reader no way to see the other question had been asked.

That is exactly the failure this project is built to prevent, so it is worth
being precise about: **a measure has no value until a filter context is
named.** It is the same fact that makes `GROUP BY` the translation of filter
context, and the reason `generate/sql.py` refuses to translate a measure until
the caller states a grain. A report page is a filter context somebody already
stated; this reads it back out.

What it does not do is guess. Power BI's filter format is a nested expression
tree with a long tail of shapes -- advanced filters, top-N, relative dates,
measure-based filters. The common ones are read into a sentence; anything else
is reported as present-but-unread, by name, rather than silently dropped. A
reader who is told "this page has a filter I could not interpret" can go and
look. A reader who is told nothing concludes there is no filter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: How a comparison kind is written in the layout, in Power BI's own numbering.
_COMPARISON = {0: "is", 1: "is more than", 2: "is at least", 3: "is less than", 4: "is at most"}


@dataclass(frozen=True)
class ReportFilter:
    """One filter, and where it applies."""

    #: "report" or "page" -- a report filter reaches every page.
    scope: str
    #: The page it belongs to. Empty for a report-level filter.
    page: str = ""
    #: What it filters on, e.g. `Sales[Status]`. Empty when unread.
    target: str = ""
    #: The whole filter as a sentence, e.g. `Sales[Status] is Sold`.
    text: str = ""
    #: False when the shape was not one this can read. `text` then says so.
    readable: bool = True

    @property
    def reaches_everything(self) -> bool:
        return self.scope == "report"


def _entity_and_property(expression: Any) -> tuple[str, str]:
    """The table and column a filter expression points at.

    Two shapes carry this. A plain `Column` names its entity directly. A
    `HierarchyLevel` -- what Power BI writes when somebody filters on a date
    hierarchy -- buries it under a `PropertyVariationSource`, which is the
    auto-generated date table standing in for the real column. The variation's
    own `Property` is the column the reader knows ("Date"), so that is what is
    reported rather than the generated table's name.
    """
    if not isinstance(expression, dict):
        return "", ""

    column = expression.get("Column")
    if isinstance(column, dict):
        source = column.get("Expression", {}).get("SourceRef", {})
        return str(source.get("Entity", "")), str(column.get("Property", ""))

    level = expression.get("HierarchyLevel")
    if isinstance(level, dict):
        hierarchy = level.get("Expression", {}).get("Hierarchy", {})
        variation = hierarchy.get("Expression", {}).get("PropertyVariationSource", {})
        source = variation.get("Expression", {}).get("SourceRef", {})
        entity = str(source.get("Entity", ""))
        # `Calendar[Date].Month` reads the way a person would say it: the
        # column they know, then the level of it being filtered on.
        column_name = str(variation.get("Property", "")) or str(
            hierarchy.get("Hierarchy", "")
        )
        return entity, f"{column_name}.{level.get('Level', '')}" if column_name else ""

    return "", ""


def _literal(value: Any) -> str:
    """One literal, with the layout's own quoting stripped."""
    if isinstance(value, dict):
        text = str(value.get("Literal", {}).get("Value", ""))
        # Values arrive as `'June'` or `1234L` -- Power BI's own encoding.
        if len(text) >= 2 and text.startswith("'") and text.endswith("'"):
            return text[1:-1]
        return text.removesuffix("L")
    return ""


def _values(condition: Any) -> list[str]:
    """The literals an `In` condition lists."""
    found: list[str] = []
    for row in condition.get("Values") or []:
        if isinstance(row, list):
            found.extend(v for v in (_literal(item) for item in row) if v)
    return found


def _condition(node: Any) -> str:
    """One `Where` clause as a phrase, or empty when the shape is unfamiliar."""
    if not isinstance(node, dict):
        return ""

    if "Not" in node:
        inner = _condition(node["Not"].get("Expression", {}))
        return f"is not {inner[3:]}" if inner.startswith("is ") else ""

    if "In" in node:
        values = _values(node["In"])
        if not values:
            return ""
        if len(values) == 1:
            return f"is {values[0]}"
        return f"is one of {', '.join(values)}"

    if "Comparison" in node:
        comparison = node["Comparison"]
        word = _COMPARISON.get(comparison.get("ComparisonKind"))
        right = _literal(comparison.get("Right", {}))
        return f"{word} {right}" if word and right else ""

    return ""


def _one(entry: Any, scope: str, page: str) -> ReportFilter | None:
    """One entry from a `filters` array."""
    if not isinstance(entry, dict):
        return None

    entity, column = _entity_and_property(entry.get("expression"))
    target = f"{entity}[{column}]" if entity and column else ""

    body = entry.get("filter")
    clauses = body.get("Where") if isinstance(body, dict) else None
    if not clauses:
        # A filter card sitting on the page with nothing selected. Not applied,
        # so not reported -- saying "filtered" here would be the false alarm.
        return None

    phrases = [
        phrase
        for phrase in (_condition(clause.get("Condition")) for clause in clauses if isinstance(clause, dict))
        if phrase
    ]
    if not target or not phrases:
        named = target or str(entry.get("name") or "a filter")
        return ReportFilter(
            scope=scope,
            page=page,
            target=target,
            text=(
                f"{named} carries a filter in a form this tool does not read, so its "
                "effect on the numbers is not described here."
            ),
            readable=False,
        )

    return ReportFilter(
        scope=scope,
        page=page,
        target=target,
        text=f"{target} {' and '.join(phrases)}",
    )


def _parse(blob: Any, scope: str, page: str) -> list[ReportFilter]:
    """The `filters` value, which the layout stores as a JSON *string*."""
    if isinstance(blob, str):
        if not blob.strip():
            return []
        try:
            blob = json.loads(blob)
        except json.JSONDecodeError:
            return []
    if not isinstance(blob, list):
        return []
    found = (_one(entry, scope, page) for entry in blob)
    return [f for f in found if f is not None]


def read_filters(document: Any) -> list[ReportFilter]:
    """Every filter a legacy `Report/Layout` document applies.

    Report-level filters come first because they reach every page, which is
    precisely the kind that surprises a reader: a card on one page showing a
    figure narrowed by something declared somewhere else entirely.
    """
    if not isinstance(document, dict):
        return []

    found = _parse(document.get("filters"), "report", "")
    for ordinal, section in enumerate(document.get("sections") or []):
        if not isinstance(section, dict):
            continue
        name = str(section.get("displayName") or f"Page {ordinal + 1}")
        found.extend(_parse(section.get("filters"), "page", name))
    return found

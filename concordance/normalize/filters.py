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

Both report formats are read. The legacy one keeps everything in a single
``Report/Layout`` blob; the newer one spreads a report over ``report.json``,
one ``page.json`` per page and one ``visual.json`` per tile, and files each
page's filters beside it. Only the first was read for a while, which meant
Store Sales -- saved in the newer format -- was reported as applying *no
filters at all* while its "New Stores" page is pinned to `Store type is Same
Store`. That is the same failure this module was written for, in the file
format it was not looking at.

What it does not do is guess. Power BI's filter format is a nested expression
tree with a long tail of shapes -- advanced filters, top-N, relative dates,
measure-based filters. The common ones are read into a sentence; anything else
is reported as present-but-unread, by name, rather than silently dropped. A
reader who is told "this page has a filter I could not interpret" can go and
look. A reader who is told nothing concludes there is no filter.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any

#: How a comparison kind is written in the layout, in Power BI's own numbering.
_COMPARISON = {0: "is", 1: "is more than", 2: "is at least", 3: "is less than", 4: "is at most"}

#: How an aggregate is written in a ranking, in Power BI's own numbering.
_AGGREGATION = {
    0: "Sum of",
    1: "Average of",
    2: "Minimum of",
    3: "Maximum of",
    4: "Count of",
    5: "Count of non-blank",
    6: "Median of",
    7: "Standard deviation of",
    8: "Variance of",
}

#: Which end of a ranking a Top-N filter keeps. 2 is descending, so the biggest.
_DIRECTION = {1: "bottom", 2: "top"}


@dataclass(frozen=True)
class ReportFilter:
    """One filter, and where it applies."""

    #: "report", "page" or "visual" -- a report filter reaches every page, a
    #: page filter every tile on one, and a visual filter one tile.
    scope: str
    #: The page it belongs to. Empty for a report-level filter.
    page: str = ""
    #: The tile it belongs to, for a visual-level filter. A title where the
    #: author wrote one, the visual's type otherwise -- an untitled card is
    #: still somewhere a reader can point at.
    visual: str = ""
    #: What it filters on, e.g. `Sales[Status]`. Empty when unread.
    target: str = ""
    #: The whole filter as a sentence, e.g. `Sales[Status] is Sold`.
    text: str = ""
    #: False when the shape was not one this can read. `text` then says so.
    readable: bool = True

    @property
    def reaches_everything(self) -> bool:
        return self.scope == "report"


def _entity(source: Any, sources: dict[str, str] | None = None) -> str:
    """The table a `SourceRef` names, whichever of the two ways it names it.

    A filter written at the top level names its table outright (`"Entity":
    "Sales"`). One written inside a query names an *alias* instead (`"Source":
    "a"`), declared in that query's own `From` clause -- the same trick SQL
    plays with `FROM Sales AS a`. Reading only the first form left every
    aliased expression looking anonymous, which is how five real Top-N filters
    came to be reported as shapes this tool could not read.
    """
    if not isinstance(source, dict):
        return ""
    entity = str(source.get("Entity", ""))
    if entity:
        return entity
    alias = str(source.get("Source", ""))
    return (sources or {}).get(alias, "")


def _entity_and_property(
    expression: Any, sources: dict[str, str] | None = None
) -> tuple[str, str]:
    """The table and column a filter expression points at.

    Three shapes carry this. A plain `Column` names its entity directly. A
    `Measure` is the same shape pointing at a measure rather than a column --
    what Power BI writes when the filter is on a computed figure rather than a
    stored one. A `HierarchyLevel` -- what Power BI writes when somebody
    filters on a date hierarchy -- buries it under a
    `PropertyVariationSource`, which is the auto-generated date table standing
    in for the real column. The variation's own `Property` is the column the
    reader knows ("Date"), so that is what is reported rather than the
    generated table's name.
    """
    if not isinstance(expression, dict):
        return "", ""

    for kind in ("Column", "Measure"):
        node = expression.get(kind)
        if isinstance(node, dict):
            source = node.get("Expression", {}).get("SourceRef", {})
            return _entity(source, sources), str(node.get("Property", ""))

    level = expression.get("HierarchyLevel")
    if isinstance(level, dict):
        hierarchy = level.get("Expression", {}).get("Hierarchy", {})
        variation = hierarchy.get("Expression", {}).get("PropertyVariationSource", {})
        source = variation.get("Expression", {}).get("SourceRef", {})
        entity = _entity(source, sources)
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


def _sources(body: Any) -> dict[str, str]:
    """Alias -> table, from one query's `From` clause.

    `[{"Name": "a", "Entity": "Association"}]` is the layout writing
    `FROM Association AS a`, and every expression below it says `"Source": "a"`.
    """
    found: dict[str, str] = {}
    if not isinstance(body, dict):
        return found
    for entry in body.get("From") or []:
        if isinstance(entry, dict) and entry.get("Entity"):
            found[str(entry.get("Name", ""))] = str(entry["Entity"])
    return found


def _subquery(body: Any, alias: str) -> dict | None:
    """The query an aliased subquery in a `From` clause holds."""
    if not isinstance(body, dict) or not alias:
        return None
    for entry in body.get("From") or []:
        if not isinstance(entry, dict) or entry.get("Name") != alias:
            continue
        query = entry.get("Expression", {}).get("Subquery", {}).get("Query")
        return query if isinstance(query, dict) else None
    return None


def _describe(expression: Any, sources: dict[str, str]) -> str:
    """What a ranking is computed on, as a person would name it.

    A Top-N filter ranks by an aggregate (`Sum of Association[Importance]`), by
    a measure the model already defines, or by a column taken as it stands.
    """
    if not isinstance(expression, dict):
        return ""

    aggregation = expression.get("Aggregation")
    if isinstance(aggregation, dict):
        word = _AGGREGATION.get(aggregation.get("Function"))
        inner = _describe(aggregation.get("Expression"), sources)
        return f"{word} {inner}" if word and inner else ""

    entity, column = _entity_and_property(expression, sources)
    return f"{entity}[{column}]" if entity and column else ""


def _top_n(node: dict, body: Any) -> str:
    """A Top-N filter, which the layout writes as membership of a subquery.

    Power BI has no "top 8" operator. It writes `WHERE column IN (subquery)`
    where the subquery selects that column, orders by something, and takes the
    first N -- exactly how you would write it in SQL. Read only as far as the
    `In`, it looks like a list of values with no values in it, which is why
    these came back unreadable. Read one level further, the subquery says
    plainly what the filter does: keep the top 8 by Sum of Importance.
    """
    alias = str((node.get("Table") or {}).get("SourceRef", {}).get("Source", ""))
    query = _subquery(body, alias)
    if query is None:
        return ""

    count = query.get("Top")
    order = (query.get("OrderBy") or [{}])[0]
    if not isinstance(count, int) or not isinstance(order, dict):
        return ""

    end = _DIRECTION.get(order.get("Direction"))
    ranked_by = _describe(order.get("Expression"), _sources(query))
    if not end or not ranked_by:
        return ""
    return f"is in the {end} {count} by {ranked_by}"


def _condition(node: Any, body: Any = None) -> str:
    """One `Where` clause as a phrase, or empty when the shape is unfamiliar.

    `body` is the whole filter the clause belongs to, which is where the
    aliases and subqueries a clause refers to are declared.
    """
    if not isinstance(node, dict):
        return ""

    if "Not" in node:
        inner = _condition(node["Not"].get("Expression", {}), body)
        return f"is not {inner[3:]}" if inner.startswith("is ") else ""

    if "In" in node:
        values = _values(node["In"])
        if not values:
            # No literals means the right-hand side is a subquery, not a list.
            return _top_n(node["In"], body)
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
        for phrase in (
            _condition(clause.get("Condition"), body)
            for clause in clauses
            if isinstance(clause, dict)
        )
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


#: A custom visual Power BI imported by identity rather than by name.
_GUID_VISUAL = re.compile(r"^PBI_CV_[0-9A-Fa-f_]+$")

#: The build stamp a custom visual carries on the end of its type name.
_STAMP = re.compile(r"\d{8,}$")


def _kind(visual_type: str) -> str:
    """A visual's type, as something a reader can act on.

    An untitled tile has to be described by its type, and two of the types in
    Microsoft's own sample are not descriptions at all: `ClusterMap1652434605854`
    carries a build stamp, and `PBI_CV_885EF3C3_31C1_4745_B2B9_20771D5AD196` is
    a bare identity. Printing either sends the reader hunting for a string that
    appears nowhere on screen, so the stamp is dropped and the identity is
    replaced by what it actually tells them: it is a custom visual.
    """
    if _GUID_VISUAL.match(visual_type):
        return "a custom visual"
    return _STAMP.sub("", visual_type) or visual_type


def _tile_name(container: Any) -> str:
    """What to call one tile, from its own title where it has one."""
    if not isinstance(container, dict):
        return ""
    try:
        config = json.loads(container.get("config") or "{}")
    except json.JSONDecodeError:
        return ""
    visual = config.get("singleVisual") or {}
    title = ((visual.get("vcObjects") or {}).get("title") or [{}])[0]
    text = (
        ((title.get("properties") or {}).get("text") or {})
        .get("expr", {})
        .get("Literal", {})
        .get("Value", "")
    )
    text = str(text).strip("'")
    return text or _kind(str(visual.get("visualType") or ""))


#: Where the newer format keeps its report definition.
_PBIR = "Report/definition/"


def _pbir_entry(entry: Any) -> dict | None:
    """One filter from the newer format, in the shape `_one` already reads.

    The two formats differ in one word: the older calls the filtered thing
    `expression` and the newer calls it `field`. Everything below that -- the
    `Where` clauses, the `From` aliases, the Top-N subqueries -- is identical,
    so translating the wrapper is the whole of the work and the reading is
    shared rather than written twice.
    """
    if not isinstance(entry, dict):
        return None
    return {
        "name": entry.get("name"),
        "expression": entry.get("field"),
        "filter": entry.get("filter"),
    }


def _pbir_filters(blob: Any, scope: str, page: str) -> list[ReportFilter]:
    """The `filterConfig.filters` array on one report, page or visual."""
    if not isinstance(blob, dict):
        return []
    entries = (blob.get("filterConfig") or {}).get("filters")
    if not isinstance(entries, list):
        return []
    found = (
        _one(translated, scope, page)
        for translated in (_pbir_entry(entry) for entry in entries)
        if translated is not None
    )
    return [f for f in found if f is not None]


def _pbir_tile_name(blob: Any) -> str:
    """What to call one tile in the newer format."""
    visual = (blob or {}).get("visual") or {}
    title = (
        ((visual.get("visualContainerObjects") or {}).get("title") or [{}])[0]
        .get("properties", {})
        .get("text", {})
        .get("expr", {})
        .get("Literal", {})
        .get("Value", "")
    )
    text = str(title).strip("'")
    return text or _kind(str(visual.get("visualType") or ""))


def read_pbir_filters(archive) -> list[ReportFilter]:
    """Every filter a report in the newer per-file format applies.

    Same order and the same scopes as `read_filters`, from files instead of
    from one blob: `report.json` reaches every page, each `page.json` reaches
    one, and each `visual.json` reaches a single tile.
    """
    from concordance.normalize.layout import _load

    try:
        names = set(archive.namelist())
    except Exception:  # noqa: BLE001 - an unreadable archive costs the filters
        return []

    found = _pbir_filters(_load(archive, f"{_PBIR}report.json"), "report", "")

    root = f"{_PBIR}pages/"
    folders = sorted(
        {
            name[len(root) :].split("/", 1)[0]
            for name in names
            if name.startswith(root) and name.endswith("/page.json")
        }
    )
    for ordinal, folder in enumerate(folders):
        definition = _load(archive, f"{root}{folder}/page.json")
        if not isinstance(definition, dict):
            continue
        page = str(definition.get("displayName") or f"Page {ordinal + 1}")
        found.extend(_pbir_filters(definition, "page", page))

        prefix = f"{root}{folder}/visuals/"
        for entry in sorted(
            name
            for name in names
            if name.startswith(prefix) and name.endswith("/visual.json")
        ):
            blob = _load(archive, entry)
            on_tile = _pbir_filters(blob, "visual", page)
            if on_tile:
                tile = _pbir_tile_name(blob)
                found.extend(replace(f, visual=tile) for f in on_tile)
    return found


def read_filters(document: Any) -> list[ReportFilter]:
    """Every filter a legacy `Report/Layout` document applies.

    Report-level filters come first because they reach every page, which is
    precisely the kind that surprises a reader: a card on one page showing a
    figure narrowed by something declared somewhere else entirely.

    Then page filters, then the ones a single tile carries. That last kind was
    missed for a while and it is the most local and the most deceiving: a card
    reading 387K beside a measure that comes to 1.2M, with the reason attached
    to that one tile and nothing else on the page. Ten of them in Microsoft's
    own sample. Every scope reaches the same conclusion -- a measure has no
    value until a filter context is named -- so leaving one out reported some
    figures as unconditional when they were not.
    """
    if not isinstance(document, dict):
        return []

    found = _parse(document.get("filters"), "report", "")
    for ordinal, section in enumerate(document.get("sections") or []):
        if not isinstance(section, dict):
            continue
        name = str(section.get("displayName") or f"Page {ordinal + 1}")
        found.extend(_parse(section.get("filters"), "page", name))
        for container in section.get("visualContainers") or []:
            if not isinstance(container, dict):
                continue
            on_tile = _parse(container.get("filters"), "visual", name)
            if on_tile:
                tile = _tile_name(container)
                found.extend(replace(f, visual=tile) for f in on_tile)
    return found

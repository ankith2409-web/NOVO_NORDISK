"""Reading the report layer of a ``.pbix``: the pages, and the tiles on them.

Everything else this project reads is the *semantic model* -- what a number
means. This reads the *report* -- where that number is shown. The two are stored
in the same file and are almost never documented together, which is the gap a
reviewer named exactly:

    "How do I understand which DAX and SQL is for which particular KPI in the
    dashboard?"                                                       -- Varun

A tile titled "Net Sales" and the measure that produces its number are the same
fact stated twice, and until now nothing joined them up: the tool could show you
the DAX for every measure in the model and still not tell you which of them was
the number on the screen. Power BI knows. It writes ``Report/Layout`` into every
``.pbix``, and each visual there carries its title and the query behind it.

**Nothing here is inferred.** A tile's title is the title the report author
typed; a tile's fields come from the visual's own ``prototypeQuery``, which is
the binding Power BI evaluates. Deliberately *not* from the ``queryRef`` alias
beside it -- that string is fixed when a field is first used and does not follow
later renames, so reading it reports fields that do not exist. See ``_bindings``
for the case in Microsoft's own sample that proves it. Where a title was never
set, this says so rather than inventing one from the fields: Power BI renders a
default in that case, and reproducing its rules would be a guess dressed as a
reading.

**What counts as a tile.** Only visuals that project at least one field. A
report is mostly furniture -- 67 of the 166 visuals in Microsoft's Sales &
Returns sample are buttons, and there are images, shapes and text boxes besides.
None of them show a number, so none of them can correlate to a measure, and
listing them would bury the ten that matter.

The format is undocumented but stable: UTF-16LE JSON, with more JSON *inside*
it as strings in ``config``. Both layers are parsed defensively, because this is
a file format Microsoft can change without telling anyone, and a report that
fails to parse must cost the caller the report layer and nothing else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: Power BI's aggregate-function codes, as written in ``Aggregation.Function``.
#: Numbered rather than named in the file, so the mapping is stated here and
#: corroborated against the aliases Power BI writes beside them: the two entries
#: carrying ``Function: 3`` are named ``Min(...)``, the thirteen carrying ``0``
#: are named ``Sum(...)``, and so on across both sample reports.
_FUNCTIONS = {
    0: "Sum",
    1: "Avg",
    2: "Count",
    3: "Min",
    4: "Max",
    5: "CountNonNull",
    6: "Median",
    7: "StandardDeviation",
    8: "Variance",
}


@dataclass(frozen=True)
class VisualField:
    """One field a tile projects, as the report refers to it."""

    #: Which well of the visual it sits in: "Values", "Y", "Category", "Legend".
    role: str
    #: The table the field belongs to, as the report names it.
    table: str
    #: The measure or column name.
    name: str
    #: "Sum", "Avg", ... or "" when the field is projected as it stands, which
    #: is what a measure reference looks like.
    aggregation: str = ""

    @property
    def qualified_name(self) -> str:
        return f"{self.table}[{self.name}]"

    @property
    def shown_as(self) -> str:
        """How the report displays it, in the report's own terms."""
        return f"{self.aggregation}({self.qualified_name})" if self.aggregation else self.qualified_name


@dataclass(frozen=True)
class Visual:
    """One tile on a report page.

    ``title`` is empty when the author never set one. That is left empty rather
    than filled in: Power BI generates a display title from the projected fields
    at render time, and guessing at it here would put words on screen that are
    in no file.
    """

    page: str
    #: "card", "barChart", "pivotTable", or a custom visual's GUID-ish id.
    visual_type: str
    title: str
    fields: tuple[VisualField, ...] = ()

    @property
    def is_titled(self) -> bool:
        return bool(self.title)


@dataclass
class ReportPage:
    """One page of the report, and the tiles on it that show data."""

    name: str
    ordinal: int
    visuals: list[Visual] = field(default_factory=list)


def read_layout(raw: bytes) -> list[ReportPage]:
    """Parse ``Report/Layout`` into pages of tiles.

    Returns an empty list rather than raising when the bytes are not a layout
    this understands. The report layer is a bonus on top of the semantic model,
    and a model must still open when its report does not parse.
    """
    document = _decode(raw)
    if not isinstance(document, dict):
        return []

    pages: list[ReportPage] = []
    for ordinal, section in enumerate(document.get("sections") or []):
        if not isinstance(section, dict):
            continue
        page = ReportPage(
            name=str(section.get("displayName") or f"Page {ordinal + 1}"),
            ordinal=ordinal,
        )
        for container in section.get("visualContainers") or []:
            visual = _visual(container, page.name)
            # Only tiles that show data. See the module docstring: a button
            # cannot correlate to a measure.
            if visual is not None and visual.fields:
                page.visuals.append(visual)
        pages.append(page)
    return pages


def _decode(raw: bytes) -> Any:
    """UTF-16LE first, because that is what Power BI writes."""
    for encoding in ("utf-16", "utf-16-le", "utf-8-sig", "utf-8"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, UnicodeError, json.JSONDecodeError):
            continue
    return None


def _visual(container: Any, page: str) -> Visual | None:
    if not isinstance(container, dict):
        return None
    config = container.get("config")
    if not isinstance(config, str):
        return None
    try:
        parsed = json.loads(config)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    # A group of visuals carries no projections of its own; its members appear
    # as their own containers, so nothing is lost by skipping the wrapper.
    single = parsed.get("singleVisual")
    if not isinstance(single, dict):
        return None

    # The real bindings, keyed by the name the projections refer to them by.
    bound = _bindings(single.get("prototypeQuery"))

    fields: list[VisualField] = []
    projections = single.get("projections")
    if isinstance(projections, dict):
        for role, items in projections.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                query_ref = str(item.get("queryRef") or "")
                parsed_field = bound.get(query_ref)
                if parsed_field is None:
                    # No entry in the query for this projection. Fall back to
                    # reading the reference itself, which is right whenever the
                    # author never renamed the field -- and is at least a name
                    # somebody can search for when they did.
                    parsed_field = _from_query_ref(query_ref)
                if parsed_field is not None:
                    fields.append(
                        VisualField(
                            role=str(role),
                            table=parsed_field.table,
                            name=parsed_field.name,
                            aggregation=parsed_field.aggregation,
                        )
                    )

    return Visual(
        page=page,
        visual_type=str(single.get("visualType") or "unknown"),
        title=_title(single),
        fields=tuple(fields),
    )


def _title(single: dict) -> str:
    """The title the author typed, or "" when they set none.

    Buried four levels down and wrapped in single quotes as a DAX-ish literal,
    which is Power BI's storage rather than anything the author sees.
    """
    objects = single.get("vcObjects")
    if not isinstance(objects, dict):
        return ""
    for entry in objects.get("title") or []:
        if not isinstance(entry, dict):
            continue
        literal = (
            entry.get("properties", {})
            .get("text", {})
            .get("expr", {})
            .get("Literal", {})
            .get("Value")
        )
        if isinstance(literal, str) and literal:
            text = literal.strip()
            if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
                text = text[1:-1]
            return text.strip()
    return ""


def _bindings(query: Any) -> dict[str, VisualField]:
    """What each of a visual's projections actually points at.

    This is the part that has to be right. A projection names a field by a
    ``queryRef`` such as ``Analysis DAX.Sales``, and that string is an *alias*
    fixed when the field was first dropped onto the visual -- it does not track
    later renames. In Microsoft's own sample the card titled "Net Sales" carries
    ``queryRef: "Analysis DAX.Sales"`` while the query beneath it selects the
    measure ``Net Sales``; there has never been a measure called ``Sales`` in
    that model. Reading the alias would have reported a tile bound to a field
    that does not exist -- a false accusation against the report, and exactly
    the kind of confident wrong answer this project exists not to give.

    So the alias is used only as a key. The binding comes from
    ``prototypeQuery``: ``From`` maps a one-letter source to a table, and each
    ``Select`` entry names a measure, a column, or an aggregation over one.
    """
    if not isinstance(query, dict):
        return {}

    tables: dict[str, str] = {}
    for source in query.get("From") or []:
        if isinstance(source, dict) and source.get("Name"):
            tables[str(source["Name"])] = str(source.get("Entity") or "")

    bound: dict[str, VisualField] = {}
    for entry in query.get("Select") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("Name") or "")
        field = _select(entry, tables)
        if name and field is not None:
            bound[name] = field
    return bound


def _select(entry: dict, tables: dict[str, str]) -> VisualField | None:
    """One ``Select`` entry: a measure, a column, or an aggregate of one."""
    aggregation = ""
    node = entry

    wrapper = node.get("Aggregation")
    if isinstance(wrapper, dict):
        aggregation = _FUNCTIONS.get(wrapper.get("Function"), "")
        node = wrapper.get("Expression")
        if not isinstance(node, dict):
            return None

    for key in ("Measure", "Column"):
        inner = node.get(key)
        if not isinstance(inner, dict):
            continue
        property_name = str(inner.get("Property") or "")
        if not property_name:
            continue
        source = (
            inner.get("Expression", {}).get("SourceRef", {}).get("Source")
            if isinstance(inner.get("Expression"), dict)
            else None
        )
        return VisualField(
            role="",
            table=tables.get(str(source), ""),
            name=property_name,
            aggregation=aggregation,
        )
    return None


def _from_query_ref(query_ref: str) -> VisualField | None:
    """Last resort: read the alias, when the query holds no entry for it.

    Split on the *last* dot, because a table name may contain one and a field
    name may not.
    """
    ref = query_ref.strip()
    if not ref:
        return None

    aggregation = ""
    for code, candidate in _FUNCTIONS.items():  # noqa: B007 -- the code is unused
        if ref.startswith(f"{candidate}(") and ref.endswith(")"):
            aggregation = candidate
            ref = ref[len(candidate) + 1 : -1].strip()
            break

    if "." not in ref:
        return VisualField(role="", table="", name=ref, aggregation=aggregation)
    table, _, name = ref.rpartition(".")
    if not name:
        return None
    return VisualField(role="", table=table.strip(), name=name.strip(), aggregation=aggregation)

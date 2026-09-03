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

**Two formats, because Power BI changed one.** Older files carry the whole
report in a single ``Report/Layout`` blob: UTF-16LE JSON, with more JSON
*inside* it as strings. Newer ones (PBIR) carry a file per visual under
``Report/definition/pages/<page>/visuals/<id>/visual.json``. Both are read here.
This is not an optional nicety: 5 of Microsoft's own recent samples use the new
format, and reading only the old one reported "this file contains no report"
about files that plainly do -- a confident wrong answer about somebody else's
work, which is the thing this project exists not to give.

The newer format is the easier of the two, because a field names its table
outright instead of through an alias that may be stale.

Both layers are parsed defensively, because this is a file format Microsoft can
change without telling anyone, and a report that fails to parse must cost the
caller the report layer and nothing else.
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
    #: Where the author put it, in the report's own coordinates. Power BI
    #: stores these as floats against a page whose size it also records; they
    #: are kept raw here and scaled by whoever draws them.
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    #: Stacking order. Two tiles may overlap, and the one with the higher `z`
    #: is the one on top -- which is what the reader saw.
    z: float = 0.0

    @property
    def is_titled(self) -> bool:
        return bool(self.title)

    @property
    def is_placed(self) -> bool:
        """False when the file recorded no size for it, so nothing can be drawn."""
        return self.width > 0 and self.height > 0


@dataclass
class ReportPage:
    """One page of the report, and the tiles on it that show data."""

    name: str
    ordinal: int
    visuals: list[Visual] = field(default_factory=list)
    #: The canvas the author laid the tiles out on, in the same coordinates the
    #: tiles use. Power BI's default is 1280x720; a report set to a different
    #: size records that here, and drawing tiles against the wrong canvas would
    #: put them in the wrong places or off the edge entirely.
    width: float = 0.0
    height: float = 0.0

    @property
    def canvas(self) -> tuple[float, float]:
        """The canvas to draw against, falling back to what the tiles need.

        A page that records no size still has tiles with coordinates, and the
        box those tiles occupy is a truthful canvas for them -- the layout is
        right relative to itself, which is what the drawing is for. The
        fallback is only ever reached when the file does not say.
        """
        if self.width > 0 and self.height > 0:
            return self.width, self.height
        placed = [v for v in self.visuals if v.is_placed]
        if not placed:
            return 0.0, 0.0
        return (
            max(v.x + v.width for v in placed),
            max(v.y + v.height for v in placed),
        )


def read_report(archive) -> list[ReportPage]:
    """The report layer of an open ``.pbix``, in whichever format it uses.

    Takes the archive rather than bytes because the newer format is spread over
    many entries. Tries the legacy blob first only because it is one read; a
    file has one format or the other, never both.
    """
    try:
        names = set(archive.namelist())
    except Exception:
        return []

    if "Report/Layout" in names:
        try:
            return read_layout(archive.read("Report/Layout"))
        except (KeyError, OSError):
            return []
    return _read_pbir(archive, names)


def read_layout(raw: bytes) -> list[ReportPage]:
    """Parse the legacy ``Report/Layout`` blob into pages of tiles.

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
        size = _placement(section)
        page = ReportPage(
            name=str(section.get("displayName") or f"Page {ordinal + 1}"),
            ordinal=ordinal,
            width=size.get("width", 0.0),
            height=size.get("height", 0.0),
        )
        for container in section.get("visualContainers") or []:
            visual = _visual(container, page.name)
            # Only tiles that show data. See the module docstring: a button
            # cannot correlate to a measure.
            if visual is not None and visual.fields:
                page.visuals.append(visual)
        pages.append(page)
    return pages


def _read_pbir(archive, names: set[str]) -> list[ReportPage]:
    """The newer per-file report format.

    ``pages.json`` gives the page order, each ``page.json`` its display name,
    and each ``visual.json`` one tile. Page order is taken from the file that
    declares it rather than from however the zip happens to be sorted -- a
    report's pages are ordered by its author, and that order is the only
    landmark a reader has for finding a tile again.
    """
    root = "Report/definition/pages/"
    order: list[str] = []
    meta = f"{root}pages.json"
    if meta in names:
        declared = _load(archive, meta)
        if isinstance(declared, dict):
            order = [str(p) for p in (declared.get("pageOrder") or [])]

    # Any page the order forgot still appears, after the ones it names.
    present = sorted({
        n[len(root):].split("/", 1)[0]
        for n in names
        if n.startswith(root) and n.endswith("/page.json")
    })
    for name in present:
        if name not in order:
            order.append(name)

    pages: list[ReportPage] = []
    for ordinal, folder in enumerate(order):
        definition = _load(archive, f"{root}{folder}/page.json")
        if not isinstance(definition, dict):
            continue
        size = _placement(definition)
        page = ReportPage(
            name=str(definition.get("displayName") or folder),
            ordinal=ordinal,
            width=size.get("width", 0.0),
            height=size.get("height", 0.0),
        )
        prefix = f"{root}{folder}/visuals/"
        for entry in sorted(n for n in names if n.startswith(prefix) and n.endswith("/visual.json")):
            visual = _pbir_visual(_load(archive, entry), page.name)
            if visual is not None and visual.fields:
                page.visuals.append(visual)
        pages.append(page)
    return pages


def _load(archive, name: str) -> Any:
    """One JSON entry, or None. ``utf-8-sig`` because these carry a BOM."""
    try:
        return json.loads(archive.read(name).decode("utf-8-sig"))
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _pbir_visual(document: Any, page: str) -> Visual | None:
    """One tile from the newer format.

    Simpler than its predecessor in the one way that matters: a projection
    names its table outright (``SourceRef.Entity``) instead of through a
    one-letter alias resolved against a ``From`` clause, so there is no stale
    alias to be misled by.
    """
    if not isinstance(document, dict):
        return None
    visual = document.get("visual")
    if not isinstance(visual, dict):
        return None

    fields: list[VisualField] = []
    state = (visual.get("query") or {}).get("queryState")
    if isinstance(state, dict):
        for role, well in state.items():
            if not isinstance(well, dict):
                continue
            for projection in well.get("projections") or []:
                if not isinstance(projection, dict):
                    continue
                field = _pbir_field(str(role), projection.get("field"))
                if field is not None:
                    fields.append(field)

    at = document.get("position")
    return Visual(
        page=page,
        visual_type=str(visual.get("visualType") or "unknown"),
        title=_title(visual.get("visualContainerObjects") or {}, key="title"),
        fields=tuple(fields),
        **_placement(at if isinstance(at, dict) else {}),
    )


def _pbir_field(role: str, field: Any) -> VisualField | None:
    """``{"Measure"|"Column": {"Expression": {"SourceRef": {"Entity": ...}}, "Property": ...}}``

    Aggregations wrap the same shape, exactly as in the older format.
    """
    if not isinstance(field, dict):
        return None

    aggregation = ""
    wrapper = field.get("Aggregation")
    if isinstance(wrapper, dict):
        aggregation = _FUNCTIONS.get(wrapper.get("Function"), "")
        field = wrapper.get("Expression")
        if not isinstance(field, dict):
            return None

    for key in ("Measure", "Column"):
        inner = field.get(key)
        if not isinstance(inner, dict):
            continue
        name = str(inner.get("Property") or "")
        if not name:
            continue
        expression = inner.get("Expression")
        entity = ""
        if isinstance(expression, dict):
            entity = str((expression.get("SourceRef") or {}).get("Entity") or "")
        return VisualField(role=role, table=entity, name=name, aggregation=aggregation)
    return None


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

    # On the container rather than inside `config`: the older format keeps a
    # visual's geometry outside the JSON string that holds everything else
    # about it.
    return Visual(
        page=page,
        visual_type=str(single.get("visualType") or "unknown"),
        title=_title(single.get("vcObjects") or {}, key="title"),
        fields=tuple(fields),
        **_placement(container),
    )


def _placement(source: Any) -> dict[str, float]:
    """Where a tile sits, from whichever of the two shapes carries it.

    Both formats use the same five keys; they differ only in where the object
    holding them lives. A value that will not read as a number is taken as zero
    rather than raising -- a tile whose geometry is unreadable is still a tile,
    and losing the whole visual over a coordinate would be a poor trade.
    """
    if not isinstance(source, dict):
        return {}
    out: dict[str, float] = {}
    for key in ("x", "y", "width", "height", "z"):
        value = source.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = float(value)
    return out


def _title(objects: Any, key: str = "title") -> str:
    """The title the author typed, or "" when they set none.

    Buried four levels down and wrapped in single quotes as a DAX-ish literal,
    which is Power BI's storage rather than anything the author sees. Both
    report formats bury it identically; only the name of the dict holding it
    differs, so both call this.
    """
    if not isinstance(objects, dict):
        return ""
    for entry in objects.get(key) or []:
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

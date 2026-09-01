"""Joining a dashboard tile to the DAX behind it, and the SQL behind that.

This is the question the reviewers kept coming back to, and the one thing they
said would finish the product:

    "How do I understand which DAX and SQL is for which particular KPI in the
    dashboard?"                                                       -- Varun
    "This is formula for total sales, and this is the formula for total profit.
    The clear distinction between the formulas and the KPIs should be there."

Both halves already existed and had never been introduced. ``layout.py`` reads
which fields a tile projects; the model holds every measure's DAX; ``sql.py``
translates that DAX. What was missing was the join between them, which is this
module and nothing more -- it computes no new facts and translates no new
expressions, it looks up.

**Resolution is by evidence, not by shape.** A ``queryRef`` of
``Analysis DAX.Sales`` does not say whether ``Sales`` is a measure or a column;
it is resolved by asking the model which one exists under that name. A field
naming something the model does not contain is reported as unresolved rather
than dropped, because that is a real finding: a tile bound to a field that is
not in this semantic model is either a broken report or a model that does not
belong to it, and both are worth saying out loud.
"""

from __future__ import annotations

from dataclasses import dataclass

from concordance.graph.csg import SemanticGraph
from concordance.model import Visual


@dataclass(frozen=True)
class ResolvedField:
    """One field of a tile, matched against the model."""

    role: str
    table: str
    name: str
    aggregation: str
    #: "measure", "column", or "" when the model has neither under this name.
    kind: str
    #: The DAX, for a measure. Empty for a column, which has no expression
    #: unless it is calculated -- in which case this carries that.
    expression: str = ""
    #: The SQL this measure translates to, when it translates at all.
    sql: str = ""
    #: Why there is no SQL, in the same plain words the dataset page uses.
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.kind)

    @property
    def qualified_name(self) -> str:
        return f"{self.table}[{self.name}]" if self.table else self.name


@dataclass(frozen=True)
class Tile:
    """A tile, with everything known about what it shows."""

    page: str
    title: str
    visual_type: str
    fields: tuple[ResolvedField, ...]

    @property
    def measures(self) -> tuple[ResolvedField, ...]:
        """The fields that are measures, which is what "the formula" means here."""
        return tuple(f for f in self.fields if f.kind == "measure")

    @property
    def is_titled(self) -> bool:
        return bool(self.title)


@dataclass(frozen=True)
class Page:
    name: str
    ordinal: int
    tiles: tuple[Tile, ...]


def correlate(
    graph: SemanticGraph,
    grain: tuple[str, ...] = (),
    dialect: str = "duckdb",
) -> tuple[Page, ...]:
    """Every page of the report, with each tile joined to its DAX and SQL.

    ``grain`` and ``dialect`` are passed through to the same translator the
    dataset page uses, so the SQL shown beside a tile is the SQL shown for that
    measure everywhere else. Two translations of one measure that disagreed
    would be worse than one.
    """
    from concordance.generate.sql import Status, to_dialect, translate_all

    model = graph.model

    measures = {
        (m.table.casefold(), m.name.casefold()): m for m in model.measures
    }
    columns = {(c.table.casefold(), c.name.casefold()): c for c in model.columns}

    # Measures are translated once for the whole report rather than once per
    # tile. The same measure appears on many tiles -- "Net Sales" is on four
    # pages of Microsoft's sample -- and translating it four times would be
    # four chances to answer differently.
    translations = {t.measure: t for t in translate_all(model, grain)}

    pages: list[Page] = []
    for page in model.report_pages:
        tiles: list[Tile] = []
        for visual in page.visuals:
            tiles.append(
                Tile(
                    page=page.name,
                    title=visual.title,
                    visual_type=visual.visual_type,
                    fields=tuple(
                        _resolve(field, measures, columns, translations, Status, dialect)
                        for field in _distinct(visual)
                    ),
                )
            )
        pages.append(Page(name=page.name, ordinal=page.ordinal, tiles=tuple(tiles)))
    return tuple(pages)


def _distinct(visual: Visual):
    """The tile's fields, without the same one twice.

    A visual can project one field into two wells -- Microsoft's map puts
    ``Net Sales`` in both ``color`` and ``size`` -- and showing its DAX twice
    under one tile is noise, not information.
    """
    seen: set[tuple[str, str, str]] = set()
    out = []
    for field in visual.fields:
        key = (field.table.casefold(), field.name.casefold(), field.aggregation)
        if key in seen:
            continue
        seen.add(key)
        out.append(field)
    return out


def _resolve(field, measures, columns, translations, Status, dialect) -> ResolvedField:
    from concordance.generate.sql import to_dialect

    key = (field.table.casefold(), field.name.casefold())

    measure = measures.get(key)
    if measure is not None:
        translation = translations.get(measure.name)
        exact = translation is not None and translation.status is Status.EXACT
        return ResolvedField(
            role=field.role,
            table=measure.table,
            name=measure.name,
            aggregation=field.aggregation,
            kind="measure",
            expression=measure.expression,
            sql=to_dialect(translation.sql, dialect) if exact else "",
            reason="" if exact else (translation.reason if translation else ""),
        )

    column = columns.get(key)
    if column is not None:
        return ResolvedField(
            role=field.role,
            table=column.table,
            name=column.name,
            aggregation=field.aggregation,
            kind="column",
            expression=column.expression or "",
        )

    return ResolvedField(
        role=field.role,
        table=field.table,
        name=field.name,
        aggregation=field.aggregation,
        kind="",
    )


def counts(pages: tuple[Page, ...]) -> dict[str, int]:
    """Enough to say, in one line, how much of the report is accounted for."""
    tiles = [tile for page in pages for tile in page.tiles]
    fields = [field for tile in tiles for field in tile.fields]
    return {
        "pages": len(pages),
        "tiles": len(tiles),
        "titled": sum(1 for t in tiles if t.is_titled),
        "with_measures": sum(1 for t in tiles if t.measures),
        "measure_fields": sum(1 for f in fields if f.kind == "measure"),
        "with_sql": sum(1 for f in fields if f.sql),
        "unresolved": sum(1 for f in fields if not f.resolved),
    }

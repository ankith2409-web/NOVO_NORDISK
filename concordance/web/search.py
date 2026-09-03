"""One question, asked once, answered across the whole model.

The interface had six places to look and no way to look in all of them. To find
a measure called "Total Sales" you first had to know that measures live on the
dataset page rather than the dashboard page, and to find the tile that shows it
you had to know the opposite. That is a question about this tool's furniture,
and nobody opening a documentation tool came to answer one.

Everything a person might name is searchable here in one pass: tables, columns,
measures, the tiles on the report, the drill-down hierarchies, and the generated
requirements. Each result carries the view that can show it and the object to
open there, so the caller can act on a hit without a second lookup.

Ranking is by how the match was made rather than by a score nobody can predict:
an exact name beats a name that starts with the query, which beats a name that
merely contains it, which beats a match found only inside a formula. Within a
tier the more central kinds come first -- a measure named `Sales` is much more
likely to be what was meant than a column called `Sales Amount`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Which kinds matter most when everything else about two hits is equal. A
#: measure is the thing this tool exists to explain, so it leads; a column is
#: the most numerous kind and the least often what somebody typed, so it trails.
_KIND_ORDER = {
    "measure": 0,
    "kpi": 1,
    "tile": 2,
    "table": 3,
    "hierarchy": 4,
    "requirement": 5,
    "column": 6,
}

#: Enough to fill the palette twice over without ever shipping a whole model.
LIMIT = 40


@dataclass(frozen=True)
class Hit:
    kind: str
    name: str
    #: Where the name sits: a table for a column or measure, a page for a tile.
    context: str
    #: One line of what it is, shown under the name.
    detail: str
    #: The view that can show this, and what to open there.
    view: str
    target: str
    rank: tuple[int, int, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "context": self.context,
            "detail": self.detail,
            "view": self.view,
            "target": self.target,
        }


def _tier(name: str, query: str) -> int | None:
    """How well `name` matches, or None when it does not.

    Four tiers rather than a similarity score, because a person who typed three
    letters can predict "starts with" and cannot predict 0.72.
    """
    folded = name.casefold()
    if folded == query:
        return 0
    if folded.startswith(query):
        return 1
    if query in folded:
        return 2
    return None


def search(graph, query: str, *, limit: int = LIMIT) -> dict[str, Any]:
    """Everything in the model whose name -- or formula -- matches `query`."""
    query = query.strip().casefold()
    if not query:
        return {"query": "", "results": [], "truncated": False}

    model = graph.model
    hits: list[Hit] = []

    def add(kind: str, name: str, context: str, detail: str, view: str,
            target: str, tier: int) -> None:
        hits.append(
            Hit(
                kind=kind,
                name=name,
                context=context,
                detail=detail,
                view=view,
                target=target,
                rank=(tier, _KIND_ORDER.get(kind, 9), name.casefold()),
            )
        )

    for table in model.user_tables():
        tier = _tier(table.name, query)
        if tier is None:
            continue
        columns = sum(1 for c in model.columns if c.table == table.name)
        measures = sum(1 for m in model.measures if m.table == table.name)
        add(
            "table",
            table.name,
            "",
            f"{columns} columns · {measures} measures",
            "dataset",
            table.name,
            tier,
        )

    for column in model.columns:
        tier = _tier(column.name, query)
        if tier is None:
            continue
        add(
            "column",
            column.name,
            column.table,
            column.expression or column.data_type or "column",
            "model",
            column.qualified_name,
            tier,
        )

    for measure in model.measures:
        # A formula match is a real way to find a measure -- "which of these
        # divides by CALCULATE" is a question a developer actually has -- but it
        # is never as good a hit as the name, so it sits in its own last tier.
        tier = _tier(measure.name, query)
        if tier is None and query in measure.expression.casefold():
            tier = 3
        if tier is None:
            continue
        add(
            "measure",
            measure.name,
            measure.table,
            measure.expression,
            "dataset",
            measure.name,
            tier,
        )

    for hierarchy in model.hierarchies:
        tier = _tier(hierarchy.name, query)
        if tier is None:
            continue
        add(
            "hierarchy",
            hierarchy.name,
            hierarchy.table,
            " → ".join(level.name for level in hierarchy.levels),
            "model",
            f"{hierarchy.table}[{hierarchy.name}]",
            tier,
        )

    measure_names = {m.name.casefold() for m in model.measures}
    for page in model.report_pages:
        for visual in page.visuals:
            if not visual.title:
                continue
            tier = _tier(visual.title, query)
            if tier is None:
                continue
            # A tile that states a figure as a number is what a reviewer points
            # at and calls a KPI. Saying which is which here means the search
            # result carries the same distinction the dashboard does.
            fields = [f.qualified_name for f in visual.fields]
            add(
                "kpi" if _is_kpi(visual, measure_names) else "tile",
                visual.title,
                page.name,
                ", ".join(fields) or visual.visual_type,
                "dashboard",
                visual.title,
                tier,
            )

    hits.sort(key=lambda hit: hit.rank)
    return {
        "query": query,
        "results": [hit.as_dict() for hit in hits[:limit]],
        "truncated": len(hits) > limit,
    }


def _is_kpi(visual, measure_names: set[str]) -> bool:
    """The same rule the dashboard uses, imported rather than restated."""
    from concordance.generate.tiles import is_kpi_visual

    return is_kpi_visual(
        visual.visual_type,
        any(field.name.casefold() in measure_names for field in visual.fields),
    )

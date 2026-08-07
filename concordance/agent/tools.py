"""Tools the agent may call against a semantic model.

Every tool reads the graph and returns extracted facts, so an answer is
grounded in the model rather than recalled from training. A model that has
never seen this .pbix can still answer questions about it correctly, and one
that hallucinates a measure name gets an explicit "not found" back rather than
a plausible invention.

The registry is deliberately broad. A narrow tool surface is what provokes a
model into inventing functions: asked "which measures exist?" with only a
``find_measure(name)`` tool available, Gemini calls a ``list_measures`` that was
never declared. Giving it the tools the questions actually need removes most of
that pressure -- and the dispatcher rejects unknown names regardless.
"""

from __future__ import annotations

from typing import Any, Callable

from concordance.generate import patterns
from concordance.graph.csg import (
    SemanticGraph,
    column_id,
    hierarchy_id,
    measure_id,
    table_id,
)
from concordance.llm.base import ToolSpec

_NO_ARGS: dict[str, Any] = {"type": "object", "properties": {}}


def _string_arg(name: str, description: str, required: bool = True) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {name: {"type": "string", "description": description}},
    }
    if required:
        schema["required"] = [name]
    return schema


class ModelTools:
    """Read-only tool surface over one semantic graph."""

    def __init__(self, graph: SemanticGraph) -> None:
        self.graph = graph
        self.model = graph.model
        self._handlers: dict[str, Callable[..., Any]] = {
            "overview": self.overview,
            "list_tables": self.list_tables,
            "list_measures": self.list_measures,
            "describe_measure": self.describe_measure,
            "describe_table": self.describe_table,
            "list_relationships": self.list_relationships,
            "list_hierarchies": self.list_hierarchies,
            "what_uses": self.what_uses,
            "search": self.search,
        }

    # -- declarations ------------------------------------------------------

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                "overview",
                "Summarise the model: its name, how many tables, measures, "
                "relationships and hierarchies it has, and anything that could "
                "not be extracted. Call this first when asked a general question.",
                _NO_ARGS,
            ),
            ToolSpec(
                "list_tables",
                "List every table with its column and measure counts.",
                _NO_ARGS,
            ),
            ToolSpec(
                "list_measures",
                "List every measure with the table it belongs to. Use before "
                "describe_measure when the exact name is not known.",
                _NO_ARGS,
            ),
            ToolSpec(
                "describe_measure",
                "Full detail for one measure: its DAX expression, what it "
                "depends on, what depends on it, and its fingerprint.",
                _string_arg("name", "Measure name, e.g. 'Serious Adverse Events'"),
            ),
            ToolSpec(
                "describe_table",
                "Columns, measures and joins for one table.",
                _string_arg("name", "Table name, e.g. 'AdverseEvent'"),
            ),
            ToolSpec(
                "list_relationships",
                "Every join: endpoints, cardinality, cross-filter direction, "
                "and whether it is active.",
                _NO_ARGS,
            ),
            ToolSpec(
                "list_hierarchies",
                "Every drill-down hierarchy and its ordered levels.",
                _NO_ARGS,
            ),
            ToolSpec(
                "what_uses",
                "What would be affected if this object changed. Accepts a "
                "measure, table or column name.",
                _string_arg("name", "Object name to trace impact for"),
            ),
            ToolSpec(
                "search",
                "Find objects whose name contains the given text. Use when the "
                "user's wording may not match a name exactly.",
                _string_arg("text", "Text to search for in object names"),
            ),
        ]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> Any:
        """Run a tool call, or explain why it could not be run.

        Unknown names are answered rather than raised: a model that invents a
        tool should be corrected inside the conversation, where it can recover
        on the next turn, not crash the process.
        """
        handler = self._handlers.get(name)
        if handler is None:
            return {
                "error": f"No tool named {name!r}.",
                "available_tools": sorted(self._handlers),
            }
        try:
            return handler(**arguments)
        except TypeError as error:
            return {
                "error": f"Wrong arguments for {name!r}: {error}",
                "expected": next(s.parameters for s in self.specs() if s.name == name),
            }

    # -- handlers ----------------------------------------------------------

    def overview(self) -> dict[str, Any]:
        summary = self.model.summary()
        return {
            "model": self.model.name,
            "source_format": self.model.source_type,
            **summary,
            "unresolved_references": [
                {"from": u.source, "to": u.target, "reason": u.reason}
                for u in self.graph.unresolved
            ],
            "not_extracted": [
                {"feature": g.feature, "count": g.count} for g in self.model.coverage_gaps
            ],
        }

    def list_tables(self) -> list[dict[str, Any]]:
        return [
            {
                "name": table.name,
                "columns": sum(1 for c in self.model.columns if c.table == table.name),
                "measures": sum(1 for m in self.model.measures if m.table == table.name),
                "kind": (
                    "system"
                    if table.is_system
                    else "measure-only" if table.is_measure_only else "data"
                ),
            }
            for table in sorted(self.model.tables, key=lambda t: t.name)
        ]

    def list_measures(self) -> list[dict[str, str]]:
        return [
            {"name": m.name, "table": m.table, "folder": m.display_folder or ""}
            for m in sorted(self.model.measures, key=lambda m: (m.table, m.name))
        ]

    def describe_measure(self, name: str) -> dict[str, Any]:
        measure = self._find_measure(name)
        if measure is None:
            return {
                "error": f"No measure named {name!r}.",
                "did_you_mean": self._similar(name, [m.name for m in self.model.measures]),
            }

        node = measure_id(measure.table, measure.name)
        behaviours = patterns.detect(measure.expression)
        return {
            "name": measure.name,
            "table": measure.table,
            "expression": measure.expression.strip(),
            "description": measure.description or "",
            "display_folder": measure.display_folder or "",
            "behaviours": [{"label": b.label, "meaning": b.description} for b in behaviours],
            "depends_on": self.graph.dependencies_of(node),
            "used_by": self.graph.dependents_of(node),
            "fingerprint": measure.fingerprint[:12],
        }

    def describe_table(self, name: str) -> dict[str, Any]:
        table = next(
            (t for t in self.model.tables if t.name.casefold() == name.casefold()), None
        )
        if table is None:
            return {
                "error": f"No table named {name!r}.",
                "did_you_mean": self._similar(name, [t.name for t in self.model.tables]),
            }

        return {
            "name": table.name,
            "kind": (
                "system" if table.is_system
                else "measure-only" if table.is_measure_only else "data"
            ),
            "columns": [
                {
                    "name": c.name,
                    "type": c.data_type,
                    "calculated": c.is_calculated,
                    "expression": (c.expression or "").strip(),
                }
                for c in self.model.columns
                if c.table == table.name
            ],
            "measures": [m.name for m in self.model.measures if m.table == table.name],
            "joins": [
                j
                for j in self.graph.join_paths()
                if j["from"] == table.name or j["to"] == table.name
            ],
            "has_power_query": table.power_query is not None,
        }

    def list_relationships(self) -> list[dict[str, Any]]:
        return self.graph.join_paths()

    def list_hierarchies(self) -> list[dict[str, Any]]:
        return [
            {
                "table": h.table,
                "name": h.name,
                "path": h.path,
                "levels": [
                    {"ordinal": lv.ordinal, "name": lv.name, "column": lv.column}
                    for lv in h.levels
                ],
            }
            for h in self.model.hierarchies
        ]

    def what_uses(self, name: str) -> dict[str, Any]:
        for node in self._candidate_nodes(name):
            if node in self.graph.graph:
                return {
                    "object": node,
                    "would_be_affected": self.graph.dependents_of(node),
                }
        return {
            "error": f"No object named {name!r} in the model.",
            "did_you_mean": self._similar(
                name,
                [m.name for m in self.model.measures]
                + [t.name for t in self.model.tables],
            ),
        }

    def search(self, text: str) -> dict[str, list[str]]:
        needle = text.casefold()
        return {
            "tables": [t.name for t in self.model.tables if needle in t.name.casefold()],
            "measures": [
                f"{m.table}[{m.name}]"
                for m in self.model.measures
                if needle in m.name.casefold()
            ],
            "columns": [
                c.qualified_name
                for c in self.model.columns
                if needle in c.name.casefold()
            ][:40],
            "hierarchies": [
                h.qualified_name for h in self.model.hierarchies if needle in h.name.casefold()
            ],
        }

    # -- helpers -----------------------------------------------------------

    def _find_measure(self, name: str):
        folded = name.casefold()
        exact = next((m for m in self.model.measures if m.name.casefold() == folded), None)
        if exact is not None:
            return exact
        # Tolerate a qualified name such as "Clinical Metrics[Patients Enrolled]".
        if "[" in name and name.endswith("]"):
            inner = name[name.index("[") + 1 : -1].casefold()
            return next(
                (m for m in self.model.measures if m.name.casefold() == inner), None
            )
        return None

    def _candidate_nodes(self, name: str) -> list[str]:
        nodes = []
        for measure in self.model.measures:
            if measure.name.casefold() == name.casefold():
                nodes.append(measure_id(measure.table, measure.name))
        for table in self.model.tables:
            if table.name.casefold() == name.casefold():
                nodes.append(table_id(table.name))
        for column in self.model.columns:
            if column.name.casefold() == name.casefold():
                nodes.append(column_id(column.table, column.name))
        for hierarchy in self.model.hierarchies:
            if hierarchy.name.casefold() == name.casefold():
                nodes.append(hierarchy_id(hierarchy.table, hierarchy.name))
        return nodes

    @staticmethod
    def _similar(name: str, candidates: list[str], limit: int = 5) -> list[str]:
        import difflib

        return difflib.get_close_matches(name, candidates, n=limit, cutoff=0.5)

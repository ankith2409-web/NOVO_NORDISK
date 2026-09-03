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
            "find_in_dax": self.find_in_dax,
            "describe_security": self.describe_security,
            "list_kpis": self.list_kpis,
            "list_calculation_groups": self.list_calculation_groups,
            "trace_source": self.trace_source,
            "find_structural_risks": self.find_structural_risks,
        }

    # -- declarations ------------------------------------------------------

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                "overview",
                "Summarise the model: its name, how many tables, measures, "
                "relationships and hierarchies it has, and anything that could "
                "not be extracted. Use when asked what the model contains or "
                "how big it is -- not for a greeting, which needs no tool.",
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
            ToolSpec(
                "find_in_dax",
                "Search inside expressions rather than names: every measure, "
                "calculated column, security filter, calculation item and KPI "
                "whose formula contains the given text. Use for questions about "
                "how something is computed rather than what it is called -- "
                "which measures use DIVIDE, what filters on a status column, "
                "where USERELATIONSHIP is invoked.",
                _string_arg("text", "Text to find in expressions, e.g. 'USERELATIONSHIP'"),
            ),
            ToolSpec(
                "describe_security",
                "Row-level and object-level security: every role, the tables it "
                "filters, the exact filter DAX, its members, and any column or "
                "table hidden from it. Use for any question about who can see "
                "what.",
                _NO_ARGS,
            ),
            ToolSpec(
                "list_kpis",
                "Every KPI: the measure it tracks and the exact target, status "
                "and trend expressions behind it.",
                _NO_ARGS,
            ),
            ToolSpec(
                "list_calculation_groups",
                "Calculation groups and their items, with each item's DAX. These "
                "rewrite other measures when applied, so they affect numbers "
                "without appearing in any measure's own expression.",
                _NO_ARGS,
            ),
            ToolSpec(
                "trace_source",
                "Where one table's data is loaded from, and what happens to it "
                "on the way in: the external systems its Power Query names, then "
                "each applied step in order, flagging the ones that can change "
                "the row count. Use for 'where does this data come from' and for "
                "why a table may not match its source.",
                _string_arg("name", "Table name, e.g. 'Batch'"),
            ),
            ToolSpec(
                "find_structural_risks",
                "Structural observations a reviewer would want flagged, computed "
                "from the model: inactive joins, bidirectional filters, two "
                "measures sharing one definition, measures nothing else uses, "
                "undocumented measures, references that did not resolve, and "
                "features that could not be extracted. These are observations, "
                "not defects -- each may be perfectly deliberate.",
                _NO_ARGS,
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
            # Per-table counts, so the overview can show where the model's
            # weight actually sits without a second request. Cheap: these are
            # already-extracted objects being counted, not anything derived.
            "by_table": [
                {
                    "name": t.name,
                    "columns": sum(1 for c in self.model.columns if c.table == t.name),
                    "measures": sum(1 for m in self.model.measures if m.table == t.name),
                }
                for t in sorted(self.model.user_tables(), key=lambda t: t.name)
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

    def find_in_dax(self, text: str) -> dict[str, Any]:
        """Search expressions rather than names.

        The question "which measures divide by something?" cannot be answered
        by `search`, which only ever looked at names -- and a measure called
        `OOS Rate` does not have "DIVIDE" anywhere in its name. Every
        expression the model holds is searched here, not just measures':
        a security filter or a calculation item can change a number just as
        surely, and leaving them out would make an empty result mean two
        different things.

        Matches carry the expression itself, so the answer above them can quote
        rather than characterise.
        """
        # Stripped before the emptiness check: a whitespace-only needle is
        # truthy, would match every expression containing a space, and so
        # would come back as "everything matched" rather than as the mistake
        # it is.
        needle = text.strip().casefold()
        if not needle:
            return {"error": "Give some text to look for in expressions."}

        measures = [
            {
                "name": m.name,
                "table": m.table,
                "expression": m.expression.strip(),
            }
            for m in self.model.measures
            if needle in m.expression.casefold()
        ]
        columns = [
            {
                "column": c.qualified_name,
                "expression": (c.expression or "").strip(),
            }
            for c in self.model.columns
            if c.is_calculated and needle in (c.expression or "").casefold()
        ]
        filters = [
            {
                "role": role.name,
                "table": permission.table,
                "expression": permission.expression.strip(),
            }
            for role in self.model.roles
            for permission in role.permissions
            if needle in permission.expression.casefold()
        ]
        items = [
            {
                "calculation_group": group.table,
                "item": item.name,
                "expression": item.expression.strip(),
            }
            for group in self.model.calculation_groups
            for item in group.items
            if needle in item.expression.casefold()
        ]
        kpis = [
            {
                "measure": kpi.measure,
                "table": kpi.table,
                "matched_in": part,
                "expression": expression.strip(),
            }
            for kpi in self.model.kpis
            for part, expression in (
                ("target", kpi.target_expression),
                ("status", kpi.status_expression),
                ("trend", kpi.trend_expression),
            )
            if expression and needle in expression.casefold()
        ]

        total = len(measures) + len(columns) + len(filters) + len(items) + len(kpis)
        return {
            "searched_for": text,
            "total_matches": total,
            "measures": measures,
            "calculated_columns": columns,
            "security_filters": filters,
            "calculation_items": items,
            "kpis": kpis,
            # Said explicitly. A model with no calculation groups and a model
            # whose calculation groups simply do not match are both an empty
            # list, and only one of them means "look somewhere else".
            "note": _searched_note(self.model),
        }

    def describe_security(self) -> dict[str, Any]:
        """Roles, their filter DAX, their members, and what is hidden from them.

        The filter expression is the whole point of returning this rather than
        a role count. "Role `Investigator` exists" tells a reader nothing about
        who sees what; `[SiteId] = USERNAME()` tells them exactly.
        """
        if not self.model.roles and not self.model.object_permissions:
            return {
                "roles": [],
                "note": (
                    "This model defines no row-level or object-level security. "
                    "Every user who can open it sees every row."
                ),
            }

        hidden_by_role: dict[str, list[dict[str, str]]] = {}
        for permission in self.model.object_permissions:
            hidden_by_role.setdefault(permission.role, []).append(
                {
                    "table": permission.table,
                    "column": permission.column or "(whole table)",
                    "permission": permission.permission,
                }
            )

        return {
            "roles": [
                {
                    "name": role.name,
                    "model_permission": role.model_permission,
                    "description": role.description or "",
                    # Named rather than counted: "two members" is not something
                    # anyone can act on, and an empty list is itself a finding.
                    "members": list(role.members),
                    "row_filters": [
                        {"table": p.table, "filter_dax": p.expression.strip()}
                        for p in role.permissions
                    ],
                    "object_level_security": hidden_by_role.get(role.name, []),
                }
                for role in sorted(self.model.roles, key=lambda r: r.name)
            ],
            "object_permissions_without_a_role": [
                entry
                for name, entries in hidden_by_role.items()
                if not any(r.name == name for r in self.model.roles)
                for entry in entries
            ],
        }

    def list_kpis(self) -> dict[str, Any]:
        """Every KPI with the three expressions behind it, quoted."""
        if not self.model.kpis:
            return {"kpis": [], "note": "This model defines no KPI objects."}
        return {
            "kpis": [
                {
                    "measure": kpi.measure,
                    "table": kpi.table,
                    "target_expression": kpi.target_expression.strip(),
                    "status_expression": kpi.status_expression.strip(),
                    "trend_expression": kpi.trend_expression.strip(),
                    "description": kpi.description or "",
                    "target_description": kpi.target_description or "",
                    "status_description": kpi.status_description or "",
                }
                for kpi in self.model.kpis
            ]
        }

    def list_calculation_groups(self) -> dict[str, Any]:
        """Calculation groups and the DAX of each item.

        Worth its own tool because these are invisible from the measures they
        change: a calculation item rewrites another measure's expression at
        query time, so a reader looking only at `describe_measure` sees a
        definition that is not what actually runs.
        """
        if not self.model.calculation_groups:
            return {
                "calculation_groups": [],
                "note": "This model defines no calculation groups.",
            }
        return {
            "calculation_groups": [
                {
                    "table": group.table,
                    "precedence": group.precedence,
                    "description": group.description or "",
                    "items": [
                        {
                            "name": item.name,
                            "ordinal": item.ordinal,
                            "expression": item.expression.strip(),
                            "format_expression": (item.format_expression or "").strip(),
                        }
                        for item in sorted(group.items, key=lambda i: i.ordinal)
                    ],
                }
                for group in self.model.calculation_groups
            ]
        }

    def trace_source(self, name: str) -> dict[str, Any]:
        """Where a table loads from, and what is done to it on the way in.

        The steps matter as much as the source. A table read from
        `patients.csv` and then filtered is not `patients.csv`, and anyone
        reconciling a figure against that file will get a different number with
        nothing to explain the gap. Steps that can change the row count are
        flagged for exactly that reason.
        """
        from concordance.normalize.mquery import extract_sources, extract_steps

        table = next(
            (t for t in self.model.tables if t.name.casefold() == name.casefold()), None
        )
        if table is None:
            return {
                "error": f"No table named {name!r}.",
                "did_you_mean": self._similar(name, [t.name for t in self.model.tables]),
            }

        if not table.power_query:
            return {
                "table": table.name,
                "sources": [],
                "steps": [],
                # Distinguished from "loads from nothing". A measure-only table
                # genuinely has no query; a table whose query was not captured
                # is a gap in extraction, and conflating them would let a
                # reader conclude the data came from nowhere.
                "note": (
                    "No Power Query is recorded for this table. Measure-only and "
                    "calculated tables have none by nature; for others this means "
                    "the query was not part of what could be extracted."
                ),
            }

        steps = extract_steps(table.power_query)
        return {
            "table": table.name,
            "sources": [
                {"kind": s.kind, "detail": s.detail, "named_by_function": s.function}
                for s in extract_sources(table.power_query)
            ],
            "steps": [
                {
                    "order": step.ordinal,
                    "name": step.name,
                    "function": step.function,
                    "effect": step.effect or "(this step's function is not one we describe)",
                    "can_change_row_count": step.changes_row_count,
                }
                for step in steps
            ],
            "steps_that_can_change_row_count": [
                step.name for step in steps if step.changes_row_count
            ],
        }

    def find_structural_risks(self) -> dict[str, Any]:
        """Structural observations, computed rather than judged.

        Every entry here is derived from the graph by a fixed rule, so this
        tool reports the same thing on every run and can be checked by hand.
        None of them is called a defect: an inactive relationship driven by
        USERELATIONSHIP is correct design, a measure nothing else uses is
        normal for one a report reads directly, and a shared definition may be
        a deliberate alias. What they have in common is only that a reviewer
        would want to have seen them.
        """
        findings: list[dict[str, Any]] = []

        inactive = [r for r in self.model.relationships if not r.is_active]
        if inactive:
            uses_userelationship = sorted(
                m.name
                for m in self.model.measures
                if "userelationship" in m.expression.casefold()
            )
            findings.append(
                {
                    "kind": "inactive_relationship",
                    "count": len(inactive),
                    "detail": [
                        f"{r.from_table}[{r.from_column}] -> {r.to_table}[{r.to_column}]"
                        for r in inactive
                    ],
                    "why_it_matters": (
                        "An inactive join only filters when a calculation invokes it "
                        "with USERELATIONSHIP. If nothing does, it never affects a "
                        "number and its purpose is not recorded anywhere in the model."
                    ),
                    "measures_invoking_userelationship": uses_userelationship,
                }
            )

        bidirectional = [
            r
            for r in self.model.relationships
            if r.cross_filter.casefold() in ("both", "bothdirections")
        ]
        if bidirectional:
            findings.append(
                {
                    "kind": "bidirectional_filter",
                    "count": len(bidirectional),
                    "detail": [
                        f"{r.from_table} <-> {r.to_table}" for r in bidirectional
                    ],
                    "why_it_matters": (
                        "Filtering in both directions can make a measure's result "
                        "depend on which way the engine resolves an ambiguous path."
                    ),
                }
            )

        # Two names over one definition. This reuses the fingerprint the whole
        # project rests on: identical canonical DAX means identical logic, so
        # this is proven rather than guessed at, the same way a rename is.
        by_fingerprint: dict[str, list[str]] = {}
        for measure in self.model.measures:
            by_fingerprint.setdefault(measure.fingerprint, []).append(
                f"{measure.table}[{measure.name}]"
            )
        duplicates = [names for names in by_fingerprint.values() if len(names) > 1]
        if duplicates:
            findings.append(
                {
                    "kind": "same_definition_under_two_names",
                    "count": len(duplicates),
                    "detail": duplicates,
                    "why_it_matters": (
                        "These measures have byte-identical canonical DAX, so they "
                        "always return the same number. Proven by fingerprint, not "
                        "inferred from the names."
                    ),
                }
            )

        orphans = sorted(
            f"{m.table}[{m.name}]"
            for m in self.model.measures
            if not self.graph.dependents_of(measure_id(m.table, m.name))
        )
        if orphans:
            findings.append(
                {
                    "kind": "measure_nothing_else_uses",
                    "count": len(orphans),
                    "detail": orphans,
                    "why_it_matters": (
                        "Nothing else in the model reads these. That is expected for "
                        "a measure a report visual uses directly -- this model does "
                        "not record report pages, so this cannot distinguish that "
                        "from one left behind."
                    ),
                }
            )

        undocumented = sorted(
            f"{m.table}[{m.name}]" for m in self.model.measures if not m.description
        )
        if undocumented:
            findings.append(
                {
                    "kind": "measure_without_a_description",
                    "count": len(undocumented),
                    "detail": undocumented[:40],
                    "why_it_matters": (
                        "With no description, the only statement of intent is the DAX "
                        "itself, so any documentation of why it exists has to come "
                        "from a person."
                    ),
                }
            )

        if self.graph.unresolved:
            findings.append(
                {
                    "kind": "reference_that_did_not_resolve",
                    "count": len(self.graph.unresolved),
                    "detail": [
                        {"from": u.source, "to": u.target, "reason": u.reason}
                        for u in self.graph.unresolved
                    ],
                    "why_it_matters": (
                        "An expression names something that could not be found in the "
                        "model, so any lineage through it is incomplete."
                    ),
                }
            )

        if self.model.coverage_gaps:
            findings.append(
                {
                    "kind": "not_extracted",
                    "count": len(self.model.coverage_gaps),
                    "detail": [
                        {"feature": g.feature, "count": g.count, "reason": g.reason}
                        for g in self.model.coverage_gaps
                    ],
                    "why_it_matters": (
                        "These model features exist but were not turned into graph "
                        "objects, so nothing above accounts for them."
                    ),
                }
            )

        return {
            "model": self.model.name,
            "findings": findings,
            "nothing_found": not findings,
            "how_to_read_this": (
                "Structural observations computed from the model, not defects. Each "
                "may be deliberate. Every one names the objects involved so it can "
                "be checked directly."
            ),
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


def _searched_note(model) -> str:
    """Which expression kinds this model actually has, for an empty result.

    Without it, "no calculation items matched" and "this model has no
    calculation groups at all" are the same empty list, and only the second one
    means the answer is elsewhere.
    """
    absent = [
        label
        for label, present in (
            ("calculated columns", any(c.is_calculated for c in model.columns)),
            ("security filters", bool(model.roles)),
            ("calculation items", bool(model.calculation_groups)),
            ("KPIs", bool(model.kpis)),
        )
        if not present
    ]
    if not absent:
        return "Searched measures, calculated columns, security filters, calculation items and KPIs."
    return (
        "Searched measures, calculated columns, security filters, calculation "
        "items and KPIs. This model defines no " + ", no ".join(absent) + "."
    )

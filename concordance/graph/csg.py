"""The Canonical Semantic Graph.

One directed graph holding every object in a model and every edge between them.
It exists so that questions the chatbot and the document generator both need to
answer -- what does this measure depend on, what breaks if this column changes,
which tables does this KPI span -- are graph traversals rather than bespoke code.

Node ids are typed and stable across extractions (``measure:Analysis DAX[Net
Sales]``), which is what lets two snapshots of the same model be compared
object by object.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from concordance.fingerprint import fingerprint_text
from concordance.model import ObjectKind, SemanticModel
from concordance.normalize.mquery import extract_sources


class EdgeKind:
    CONTAINS = "contains"      # table -> column
    DEFINES = "defines"        # table -> measure
    REFERENCES = "references"  # measure -> column | measure
    JOINS = "joins"            # table -> table
    LOADS = "loads"            # table -> source it is loaded from


def table_id(name: str) -> str:
    return f"table:{name}"


def column_id(table: str, name: str) -> str:
    return f"column:{table}[{name}]"


def measure_id(table: str, name: str) -> str:
    return f"measure:{table}[{name}]"


def hierarchy_id(table: str, name: str) -> str:
    return f"hierarchy:{table}[{name}]"


def source_id(kind: str, detail: str) -> str:
    """Identified by what it points at, not by which table found it.

    Two tables loaded from one workbook share a node, which is the whole
    reason to draw it: "these four tables all come from one spreadsheet on
    somebody's laptop" is a finding, and one node per table would hide it.
    """
    return f"source:{kind}:{detail}"


@dataclass
class UnresolvedReference:
    """A dependency naming an object that is not in the model.

    Recorded rather than dropped: an unresolved reference is usually a real
    finding (a renamed column, a broken measure) and the documentation should
    say so instead of quietly omitting the edge.
    """
    source: str
    target: str
    reason: str


class SemanticGraph:
    """A model as a queryable graph."""

    def __init__(self, model: SemanticModel) -> None:
        self.model = model
        # MultiDiGraph, not DiGraph: two tables can be joined more than once.
        # A role-playing date dimension -- Batch[ManufactureDate] and
        # Batch[ReleaseDate] both pointing at Calendar[Date] -- is ordinary in
        # real models, and a DiGraph silently keeps only the last one written.
        self.graph = nx.MultiDiGraph()
        self.unresolved: list[UnresolvedReference] = []
        self._build()

    # -- construction -------------------------------------------------------

    def _build(self) -> None:
        for table in self.model.tables:
            self.graph.add_node(
                table_id(table.name),
                kind=ObjectKind.TABLE.value,
                name=table.name,
                fingerprint=table.fingerprint,
                is_system=table.is_system,
                # A measure container holds definitions rather than data. The
                # model has always known this; not carrying it into the graph
                # meant anything reading the graph had to guess, and a reader
                # seeing the one hidden placeholder column that TMDL requires on
                # such a table has no way to tell it apart from real data.
                is_measure_only=table.is_measure_only,
                has_power_query=table.power_query is not None,
            )
            self._add_sources(table)

        for column in self.model.columns:
            node = column_id(column.table, column.name)
            kind = (
                ObjectKind.CALCULATED_COLUMN.value
                if column.is_calculated
                else ObjectKind.COLUMN.value
            )
            self.graph.add_node(
                node,
                kind=kind,
                name=column.name,
                table=column.table,
                data_type=column.data_type,
                expression=column.expression,
                fingerprint=column.fingerprint,
            )
            self.graph.add_edge(
                table_id(column.table), node, key=EdgeKind.CONTAINS, kind=EdgeKind.CONTAINS
            )

        for measure in self.model.measures:
            node = measure_id(measure.table, measure.name)
            self.graph.add_node(
                node,
                kind=ObjectKind.MEASURE.value,
                name=measure.name,
                table=measure.table,
                expression=measure.expression,
                fingerprint=measure.fingerprint,
                display_folder=measure.display_folder,
                description=measure.description,
            )
            self.graph.add_edge(
                table_id(measure.table), node, key=EdgeKind.DEFINES, kind=EdgeKind.DEFINES
            )

        self._add_hierarchies()
        self._link_measure_dependencies()
        self._link_calculated_columns()

        for rel in self.model.relationships:
            self.graph.add_edge(
                table_id(rel.from_table),
                table_id(rel.to_table),
                # Keyed by the joining columns so two relationships between the
                # same pair of tables coexist instead of overwriting each other.
                # Re-extracting the same model still yields one edge per join.
                key=f"{rel.from_column}->{rel.to_column}",
                kind=EdgeKind.JOINS,
                from_column=rel.from_column,
                to_column=rel.to_column,
                cardinality=rel.cardinality,
                cross_filter=rel.cross_filter,
                is_active=rel.is_active,
                fingerprint=rel.fingerprint,
            )

    def _add_sources(self, table) -> None:
        """Give a table's Power Query source a node, so lineage does not stop here.

        The edge runs table -> source, matching `references`: in this graph an
        out-edge means "reads", so the reader points at what it reads. Data
        flows the other way, which is tempting to draw instead, but an edge set
        where half the arrows mean "reads" and half mean "feeds" cannot be
        walked by anything without special-casing each kind.

        A table whose query names nothing recognisable gets no node rather than
        an "unknown source" placeholder. The two are different claims -- one
        says where the data comes from, the other only that this module could
        not tell -- and drawing the second as though it were the first is how a
        diagram becomes confidently wrong.
        """
        if not table.power_query:
            return
        for reference in extract_sources(table.power_query):
            node = source_id(reference.kind, reference.detail)
            self.graph.add_node(
                node,
                kind=ObjectKind.SOURCE.value,
                name=reference.label,
                source_kind=reference.kind,
                detail=reference.detail,
                # Named so a reader can check the claim against the query
                # itself rather than taking this module's word for it.
                via=reference.function,
                fingerprint=fingerprint_text(f"{reference.kind}:{reference.detail}"),
            )
            self.graph.add_edge(
                table_id(table.name), node, key=EdgeKind.LOADS, kind=EdgeKind.LOADS
            )

    def _link_measure_dependencies(self) -> None:
        by_name = {
            m.name.casefold(): measure_id(m.table, m.name) for m in self.model.measures
        }
        columns = {
            (c.table.casefold(), c.name.casefold()): column_id(c.table, c.name)
            for c in self.model.columns
        }

        # Power BI's What-If parameter tables are built by GENERATESERIES and
        # their columns never reach the stored schema, so references into them
        # cannot be resolved from extracted metadata. That is a real limit worth
        # naming precisely rather than reporting as a missing column.
        tables_with_columns = {c.table.casefold() for c in self.model.columns}
        known_tables = {t.name.casefold() for t in self.model.tables}

        for measure in self.model.measures:
            source = measure_id(measure.table, measure.name)

            for table, column in measure.depends_on_columns:
                target = columns.get((table.casefold(), column.casefold()))
                if target is None:
                    self.unresolved.append(
                        UnresolvedReference(
                            source,
                            f"{table}[{column}]",
                            self._why_unresolved(
                                table, known_tables, tables_with_columns
                            ),
                        )
                    )
                    continue
                self.graph.add_edge(
                    source, target, key=EdgeKind.REFERENCES, kind=EdgeKind.REFERENCES
                )

            for name in measure.depends_on_measures:
                target = by_name.get(name.casefold())
                if target is None:
                    self.unresolved.append(
                        UnresolvedReference(source, f"[{name}]", "measure not found")
                    )
                    continue
                if target != source:  # a measure referencing itself adds nothing
                    self.graph.add_edge(
                        source, target,
                        key=EdgeKind.REFERENCES, kind=EdgeKind.REFERENCES,
                    )

            # A whole-table read such as COUNTROWS(Patient). Resolved by the
            # adapter against real table names, so anything here exists.
            for name in measure.depends_on_tables:
                self.graph.add_edge(
                    source, table_id(name),
                    key=EdgeKind.REFERENCES, kind=EdgeKind.REFERENCES,
                )

    def _add_hierarchies(self) -> None:
        """Add hierarchies and bind each level to the column that supplies it."""
        columns = {
            (c.table.casefold(), c.name.casefold()): column_id(c.table, c.name)
            for c in self.model.columns
        }

        for hierarchy in self.model.hierarchies:
            node = hierarchy_id(hierarchy.table, hierarchy.name)
            self.graph.add_node(
                node,
                kind=ObjectKind.HIERARCHY.value,
                name=hierarchy.name,
                table=hierarchy.table,
                fingerprint=hierarchy.fingerprint,
                path=hierarchy.path,
                levels=[
                    {"ordinal": lv.ordinal, "name": lv.name, "column": lv.column}
                    for lv in hierarchy.levels
                ],
                is_hidden=hierarchy.is_hidden,
                display_folder=hierarchy.display_folder,
                description=hierarchy.description,
            )
            self.graph.add_edge(
                table_id(hierarchy.table), node, kind=EdgeKind.CONTAINS
            )

            # Each level depends on a real column, so changing that column shows
            # up in the hierarchy's impact set like any other dependency.
            for level in hierarchy.levels:
                target = columns.get(
                    (hierarchy.table.casefold(), level.column.casefold())
                )
                if target is None:
                    self.unresolved.append(
                        UnresolvedReference(
                            node,
                            f"{hierarchy.table}[{level.column}]",
                            "hierarchy level column not found in table",
                        )
                    )
                    continue
                self.graph.add_edge(
                    node, target, key=EdgeKind.REFERENCES, kind=EdgeKind.REFERENCES
                )

    @staticmethod
    def _why_unresolved(
        table: str, known_tables: set[str], tables_with_columns: set[str]
    ) -> str:
        key = table.casefold()
        if key not in known_tables:
            return "table not in model"
        if key not in tables_with_columns:
            return "calculated/parameter table -- columns not in stored schema"
        return "column not found in table"

    def _link_calculated_columns(self) -> None:
        from concordance.normalize.dax import extract_references

        columns = {
            (c.table.casefold(), c.name.casefold()): column_id(c.table, c.name)
            for c in self.model.columns
        }

        for column in self.model.columns:
            if not column.is_calculated:
                continue
            source = column_id(column.table, column.name)
            refs = extract_references(column.expression or "")
            # An unqualified [Name] in a calculated column resolves against its
            # own table.
            candidates = set(refs.columns) | {
                (column.table, name) for name in refs.unqualified
            }
            for table, name in candidates:
                target = columns.get((table.casefold(), name.casefold()))
                if target is not None and target != source:
                    self.graph.add_edge(
                    source, target, key=EdgeKind.REFERENCES, kind=EdgeKind.REFERENCES
                )

    # -- queries ------------------------------------------------------------

    def nodes_of_kind(self, kind: str) -> list[str]:
        return [n for n, d in self.graph.nodes(data=True) if d.get("kind") == kind]

    def dependencies_of(self, node: str) -> list[str]:
        """Everything ``node`` reads, transitively."""
        if node not in self.graph:
            return []
        seen: set[str] = set()
        stack = [node]
        while stack:
            current = stack.pop()
            for _, target, data in self.graph.out_edges(current, data=True):
                if data.get("kind") == EdgeKind.REFERENCES and target not in seen:
                    seen.add(target)
                    stack.append(target)
        return sorted(seen)

    def dependents_of(self, node: str) -> list[str]:
        """Everything that would be affected if ``node`` changed."""
        if node not in self.graph:
            return []
        seen: set[str] = set()
        stack = [node]
        while stack:
            current = stack.pop()
            for source, _, data in self.graph.in_edges(current, data=True):
                if data.get("kind") == EdgeKind.REFERENCES and source not in seen:
                    seen.add(source)
                    stack.append(source)
        return sorted(seen)

    def join_paths(self) -> list[dict]:
        return [
            {
                "from": data_source.removeprefix("table:"),
                "to": target.removeprefix("table:"),
                "from_column": data["from_column"],
                "to_column": data["to_column"],
                "cardinality": data["cardinality"],
                "cross_filter": data["cross_filter"],
                "is_active": data["is_active"],
                "fingerprint": data["fingerprint"],
            }
            for data_source, target, data in self.graph.edges(data=True)
            if data.get("kind") == EdgeKind.JOINS
        ]

    def fingerprints(self) -> dict[str, str]:
        """Every fingerprinted object, keyed by node id.

        This mapping is the unit of comparison for drift detection in week 3.
        """
        return {
            node: data["fingerprint"]
            for node, data in self.graph.nodes(data=True)
            if data.get("fingerprint")
        }

    def stats(self) -> dict[str, int]:
        kinds: dict[str, int] = {}
        for _, data in self.graph.nodes(data=True):
            kinds[data.get("kind", "unknown")] = kinds.get(data.get("kind", "unknown"), 0) + 1
        edges: dict[str, int] = {}
        for _, _, data in self.graph.edges(data=True):
            edges[data.get("kind", "unknown")] = edges.get(data.get("kind", "unknown"), 0) + 1
        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
            **{f"node:{k}": v for k, v in sorted(kinds.items())},
            **{f"edge:{k}": v for k, v in sorted(edges.items())},
            "unresolved_references": len(self.unresolved),
            "coverage_gaps": len(self.model.coverage_gaps),
        }

    def to_dict(self) -> dict:
        """Serialise for the API and for snapshot storage."""
        return {
            "model": {
                "name": self.model.name,
                "source_path": self.model.source_path,
                "source_type": self.model.source_type,
                "summary": self.model.summary(),
            },
            "nodes": [
                {"id": node, **data} for node, data in sorted(self.graph.nodes(data=True))
            ],
            "edges": [
                {"source": s, "target": t, **data}
                for s, t, data in sorted(
                    # (source, target) alone is no longer unique on a
                    # MultiDiGraph, so the edge kind and columns join the sort
                    # key to keep serialisation deterministic.
                    self.graph.edges(data=True),
                    key=lambda e: (
                        e[0], e[1], e[2].get("kind", ""),
                        e[2].get("from_column", ""), e[2].get("to_column", ""),
                    ),
                )
            ],
            "unresolved": [
                {"source": u.source, "target": u.target, "reason": u.reason}
                for u in self.unresolved
            ],
            # Carried into the serialised graph so that anything consuming it
            # downstream -- document generation, drift comparison -- can see what
            # this extraction did not cover, instead of assuming completeness.
            "coverage_gaps": [
                {"feature": g.feature, "count": g.count, "reason": g.reason}
                for g in self.model.coverage_gaps
            ],
            "stats": self.stats(),
        }

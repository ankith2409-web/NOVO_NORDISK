"""Emit the model's lineage as OpenLineage, so a catalog can read it.

Concordance already knows which column every measure reads, through which
joins, and which source file every table was loaded from. That knowledge is
useful to exactly one audience inside this tool and to a much larger one
outside it: a company that runs a data catalog -- Marquez, DataHub, Atlan,
Collibra -- has somewhere to put lineage, and no way to get Power BI's into it.
Nothing here is new analysis. It is the analysis already done, written in the
format the rest of that ecosystem reads.

**Static lineage, not run lineage.** OpenLineage's centre of gravity is a job
running and producing a dataset, reported as a ``RunEvent``. A semantic model
is not a run: it is a standing description of how figures are defined, true
until someone edits it. The spec has a shape for exactly that -- ``DatasetEvent``,
which carries dataset metadata and is defined as unable to include a ``job`` or
a ``run`` -- and that is what this emits. Emitting a ``RunEvent`` with an
invented run id would be claiming an execution that never happened.

**What is asserted, and how it is known.** The ``columnLineage`` facet says which
input columns each output field derives from. For a measure, that is resolved
by the same compiler that generates its SQL, so it follows measure references
and calculated columns down to base columns rather than stopping at the first
name it meets: ``OOS Rate`` reports ``TestResult.ResultStatus``, not
``[OOS Results]``. A measure the compiler refuses to translate has no entry --
the honest answer, since the alternative is guessing at what it reads.

**One thing this does not claim.** OpenLineage's naming specification registers
schemes for warehouses and object stores; it has none for a Power BI semantic
model. The namespace below is therefore stated by this tool rather than
canonical, and is stable and documented rather than correct-by-standard. A
catalog that wants a different one can be told: it is one argument.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

#: Identifies what produced these events, as every facet is required to.
PRODUCER = "https://github.com/ankith2409-web/NOVO_NORDISK"

_SPEC = "https://openlineage.io/spec/2-0-2/OpenLineage.json"
_FACET_SPEC = (
    "https://openlineage.io/spec/facets/1-2-0/ColumnLineageDatasetFacet.json"
)
_SCHEMA_SPEC = "https://openlineage.io/spec/facets/1-1-1/SchemaDatasetFacet.json"

#: How a Power BI object is addressed. Not from OpenLineage's naming spec,
#: which has no scheme for a semantic model -- see the module docstring.
DEFAULT_NAMESPACE = "powerbi"


def _facet(schema_url: str, **fields: Any) -> dict[str, Any]:
    """A facet with the two keys every facet is required to carry."""
    return {"_producer": PRODUCER, "_schemaURL": schema_url, **fields}


@dataclass(frozen=True)
class Emitted:
    """The events, and what was left out of them.

    The second half matters as much as the first. A catalog fed lineage that
    silently omits half the measures shows a model that looks simpler than it
    is, which is the same failure this project exists to prevent -- committed
    against a different audience.
    """

    events: tuple[dict[str, Any], ...]
    #: Measures with no column lineage, and why, so the omission is reportable.
    omitted: tuple[tuple[str, str], ...]

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(list(self.events), indent=indent)


def emit(graph, namespace: str = DEFAULT_NAMESPACE) -> Emitted:
    """One ``DatasetEvent`` per table, with schema and column lineage."""
    from concordance.generate.sql import Status, translate_all

    model = graph.model
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tables = {t.name for t in model.user_tables()}

    # Where each table was loaded from, for dataset-level lineage back out of
    # Power BI. Read off the graph rather than recomputed: the M parser already
    # resolved these and a second implementation would be a second thing to
    # keep true.
    sources: dict[str, list[str]] = {}
    for edge in graph.to_dict().get("edges", []):
        if edge.get("kind") == "loads":
            table = edge["source"].split(":", 1)[1]
            sources.setdefault(table, []).append(edge["target"].split(":", 1)[1])

    # A measure's real column dependencies, resolved through the measures and
    # calculated columns it is defined over.
    reads: dict[str, list[tuple[str, str]]] = {}
    omitted: list[tuple[str, str]] = []
    for translation in translate_all(model):
        if translation.status is Status.EXACT:
            reads[translation.measure] = sorted(translation.reads_columns)
        else:
            omitted.append((translation.measure, translation.reason))

    events: list[dict[str, Any]] = []
    for table in sorted(model.user_tables(), key=lambda t: t.name):
        columns = [c for c in model.columns if c.table == table.name]
        measures = [m for m in model.measures if m.table == table.name]

        fields = [
            {"name": c.name, "type": c.data_type or "unknown"} for c in columns
        ] + [{"name": m.name, "type": "measure"} for m in measures]

        lineage: dict[str, Any] = {}
        for column in columns:
            inputs = _calculated_from(model, column, namespace, tables)
            if inputs:
                lineage[column.name] = {"inputFields": inputs}
        for measure in measures:
            inputs = [
                {
                    "namespace": namespace,
                    "name": read_table,
                    "field": read_column,
                    "transformations": [
                        {
                            "type": "INDIRECT",
                            "subtype": "AGGREGATION",
                            "description": "read by a DAX measure",
                        }
                    ],
                }
                for read_table, read_column in reads.get(measure.name, ())
                if read_table in tables
            ]
            if inputs:
                lineage[measure.name] = {"inputFields": inputs}

        facets: dict[str, Any] = {
            "schema": _facet(_SCHEMA_SPEC, fields=fields),
        }
        if lineage:
            facet: dict[str, Any] = {"fields": lineage}
            if sources.get(table.name):
                # Dataset-level provenance: where the table itself came from,
                # which the per-field lineage does not carry.
                facet["dataset"] = [
                    {"namespace": "file", "name": origin, "field": ""}
                    for origin in sorted(sources[table.name])
                ]
            facets["columnLineage"] = _facet(_FACET_SPEC, **facet)

        events.append(
            {
                "eventTime": at,
                "producer": PRODUCER,
                "schemaURL": _SPEC,
                "dataset": {
                    "namespace": namespace,
                    "name": f"{model.name}.{table.name}",
                    "facets": facets,
                },
            }
        )

    return Emitted(events=tuple(events), omitted=tuple(sorted(omitted)))


def _calculated_from(model, column, namespace: str, tables: set[str]) -> list[dict]:
    """The columns a calculated column is computed from."""
    if not getattr(column, "expression", None):
        return []
    from concordance.normalize.dax import extract_references

    # `extract_references` returns resolved (table, column) pairs, so nothing
    # here re-parses `Table[Column]` out of a string -- which is the sort of
    # second, weaker implementation this codebase avoids on principle.
    return [
        {
            "namespace": namespace,
            "name": table,
            "field": name,
            "transformations": [
                {
                    "type": "DIRECT",
                    "subtype": "TRANSFORMATION",
                    "description": "computed by a calculated column",
                }
            ],
        }
        for table, name in sorted(extract_references(column.expression).columns)
        if table in tables
    ]

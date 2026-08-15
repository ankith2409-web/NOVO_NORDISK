"""Point-in-time records of a semantic model.

A snapshot is the set of fingerprints a model had at one moment, plus enough
detail to explain a change in human terms later. Comparing two snapshots is
what turns "the documentation might be stale" into "REQ-F-b06dae is bound to a
measure whose definition moved on 12 March".

Snapshots deliberately store the *fingerprints*, not the derived requirements.
Fingerprints are ground truth about the model; requirements are an
interpretation of them, and interpretation improves. Re-deriving requirements at
comparison time means an old snapshot benefits from a better deriver rather than
being frozen against the version that wrote it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from concordance.graph.csg import SemanticGraph

#: Bumped if the stored shape changes in a way older files cannot satisfy.
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ObjectRecord:
    """One fingerprinted object, with what is needed to describe a change."""

    node_id: str
    kind: str
    fingerprint: str
    #: The expression, drill path or join label -- whatever the object's
    #: fingerprint was computed over, in a form a person can read.
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "fingerprint": self.fingerprint,
            "detail": self.detail,
        }

    @staticmethod
    def from_dict(data: dict[str, str]) -> "ObjectRecord":
        return ObjectRecord(
            node_id=data["node_id"],
            kind=data.get("kind", "unknown"),
            fingerprint=data["fingerprint"],
            detail=data.get("detail", ""),
        )


@dataclass(frozen=True)
class Snapshot:
    """Everything fingerprinted in one model at one moment."""

    model_name: str
    source_path: str
    source_type: str
    taken_at: str
    label: str
    objects: dict[str, ObjectRecord] = field(default_factory=dict)

    @property
    def fingerprints(self) -> dict[str, str]:
        return {node: record.fingerprint for node, record in self.objects.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "model_name": self.model_name,
            "source_path": self.source_path,
            "source_type": self.source_type,
            "taken_at": self.taken_at,
            "label": self.label,
            "objects": [record.to_dict() for record in self.objects.values()],
        }

    def save(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=False), encoding="utf-8"
        )
        return destination

    @staticmethod
    def load(path: str | Path) -> "Snapshot":
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(f"no such snapshot: {source}")

        data = json.loads(source.read_text(encoding="utf-8"))
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"{source} was written by schema version {version!r}, but this "
                f"build reads version {SCHEMA_VERSION}. Re-take the snapshot."
            )

        records = [ObjectRecord.from_dict(item) for item in data.get("objects", [])]
        return Snapshot(
            model_name=data["model_name"],
            source_path=data.get("source_path", ""),
            source_type=data.get("source_type", ""),
            taken_at=data.get("taken_at", ""),
            label=data.get("label", ""),
            objects={record.node_id: record for record in records},
        )


def take(graph: SemanticGraph, label: str = "") -> Snapshot:
    """Record the current state of a model."""
    objects: dict[str, ObjectRecord] = {}

    for node, data in graph.graph.nodes(data=True):
        fingerprint = data.get("fingerprint")
        if not fingerprint:
            continue
        objects[node] = ObjectRecord(
            node_id=node,
            kind=data.get("kind", "unknown"),
            fingerprint=fingerprint,
            detail=_detail_of(data),
        )

    # Relationships live on edges rather than nodes, and a join changing
    # cardinality or direction changes what every measure over it reports.
    for source, target, data in graph.graph.edges(data=True):
        fingerprint = data.get("fingerprint")
        if not fingerprint or data.get("kind") != "joins":
            continue
        node_id = (
            f"relationship:{source.removeprefix('table:')}"
            f"[{data.get('from_column', '')}]"
            f"->{target.removeprefix('table:')}[{data.get('to_column', '')}]"
        )
        objects[node_id] = ObjectRecord(
            node_id=node_id,
            kind="relationship",
            fingerprint=fingerprint,
            detail=(
                f"{data.get('cardinality', '')} "
                f"{data.get('cross_filter', '')} "
                f"{'active' if data.get('is_active') else 'inactive'}"
            ).strip(),
        )

    return Snapshot(
        model_name=graph.model.name,
        source_path=graph.model.source_path,
        source_type=graph.model.source_type,
        taken_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        label=label,
        objects=objects,
    )


def _detail_of(data: dict[str, Any]) -> str:
    """The human-readable form of whatever this object's fingerprint covers."""
    for key in ("expression", "path", "power_query"):
        value = data.get(key)
        if value:
            return str(value).strip()
    return str(data.get("name", ""))

"""The JSON API the browser talks to.

Kept apart from the HTTP handler for the same reason ``SessionStore`` is: every
endpoint here is a plain function from a request to a payload, so it can be
tested without opening a socket. The handler's only job is routing, headers and
session cookies.

Two things shape the design.

*Nothing here reads a path supplied by the caller.* Drift needs a second model
and reconciliation needs a warehouse, and the obvious way to get them -- a query
parameter naming a file -- would turn a read-only local server into an arbitrary
file reader. Both are instead configured when the server starts, so the client
can only ask *whether* to compare, never *what against*. Endpoints that were not
configured say so plainly rather than pretending the feature does not exist.

*Read-only endpoints do not touch session state.* The graph is built once and
never mutated, so every visitor can share it. Only the chat needs a session,
because only the chat accumulates history.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable

from concordance.drift import snapshot as snap
from concordance.drift.compare import compare
from concordance.generate.requirements import (
    Confidence,
    Kind,
    Requirement,
    RequirementDeriver,
)
from concordance.graph.csg import SemanticGraph

#: Query parameters arrive as ``dict[str, list[str]]`` from ``parse_qs``.
Params = dict[str, list[str]]


class ApiError(Exception):
    """A failure with an HTTP status and a message the UI can show a person."""

    def __init__(self, status: HTTPStatus, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra

    def payload(self) -> dict[str, Any]:
        return {"error": self.message, **self.extra}


@dataclass
class ApiContext:
    """Everything the API is allowed to reach.

    Whatever is absent here is genuinely unavailable to a request -- which is
    the point. ``compare_to`` and ``warehouse`` are supplied at startup by
    whoever ran the command, never by the browser.
    """

    graph: SemanticGraph
    compare_to: SemanticGraph | None = None
    compare_label: str = ""
    warehouse: Path | None = None
    warehouse_schema: str = "main"

    def requirements(self, kind: Kind) -> list[Requirement]:
        """Derive requirements on demand.

        Not cached: derivation is fast, and a stale cache is a worse failure
        than a repeated computation for a tool whose whole claim is that the
        document matches the model.
        """
        return [r for r in RequirementDeriver(self.graph).derive() if r.kind is kind]


@dataclass
class ModelRegistry:
    """The models this server was started with, addressable by name.

    A request selects one with ``?model=``. That parameter names a key in this
    registry -- fixed when the server started -- and never a path, which keeps
    the invariant the rest of this module rests on: the browser can choose
    among what an operator loaded, and cannot reach anything they did not.

    Each model keeps its own comparison sources, so serving several does not
    mean they share a warehouse or a drift baseline.
    """

    contexts: dict[str, ApiContext]
    default: str

    @classmethod
    def of(cls, context: ApiContext) -> ModelRegistry:
        """Wrap a single context, so one model is just a registry of one."""
        name = context.graph.model.name
        return cls(contexts={name: context}, default=name)

    def resolve(self, params: Params) -> ApiContext:
        requested = (params.get("model") or [""])[0].strip()
        if not requested:
            return self.contexts[self.default]
        if requested not in self.contexts:
            # Deliberately does not repeat what was asked for. Reflecting
            # unbounded caller input back in an error is a habit worth not
            # having, and the useful half of the answer is what *is* loaded --
            # which is also the half a legitimate caller needs.
            raise ApiError(
                HTTPStatus.NOT_FOUND,
                "that model is not loaded on this server",
                loaded=sorted(self.contexts),
            )
        return self.contexts[requested]

    def describe(self) -> dict[str, Any]:
        return {
            "default": self.default,
            "models": [
                {
                    "name": name,
                    "source_format": context.graph.model.source_type,
                    "measures": len(context.graph.model.measures),
                    "tables": len(context.graph.model.user_tables()),
                    "capabilities": {
                        "drift": context.compare_to is not None,
                        "reconcile": context.warehouse is not None,
                    },
                }
                for name, context in sorted(self.contexts.items())
            ],
        }


# -- serialisation ------------------------------------------------------------

def _requirement_dict(requirement: Requirement) -> dict[str, Any]:
    """One requirement, with the evidence that binds it to the model.

    ``evidence`` is always included rather than offered as a second request:
    the interface shows provenance on every claim, and a round trip per row
    would make that too expensive to do consistently.
    """
    return {
        "id": requirement.id,
        "kind": requirement.kind.value,
        "category": requirement.category,
        "statement": requirement.statement,
        "rationale": requirement.rationale,
        "confidence": requirement.confidence.value,
        "needs_review": requirement.needs_review,
        "evidence": [
            {
                "node_id": e.node_id,
                "fingerprint": e.fingerprint,
                "short_fingerprint": e.fingerprint[:12],
                "detail": e.detail,
            }
            for e in requirement.evidence
        ],
    }


def _one(params: Params, key: str, *, required: bool = True) -> str:
    values = params.get(key) or []
    value = values[0].strip() if values else ""
    if required and not value:
        raise ApiError(
            HTTPStatus.BAD_REQUEST, f"the {key!r} parameter is required and must not be empty"
        )
    return value


# -- endpoints ----------------------------------------------------------------

def overview(context: ApiContext, params: Params) -> dict[str, Any]:
    """Counts, plus what could not be read.

    Coverage gaps ride along with the summary deliberately. A limitation that
    needs a second request to discover is a limitation most callers will never
    render.
    """
    from concordance.agent.tools import ModelTools

    payload = ModelTools(context.graph).overview()
    payload["capabilities"] = {
        "drift": context.compare_to is not None,
        "reconcile": context.warehouse is not None,
    }
    return payload


def graph(context: ApiContext, params: Params) -> dict[str, Any]:
    """The whole semantic graph, for the canvas."""
    return context.graph.to_dict()


def tables(context: ApiContext, params: Params) -> dict[str, Any]:
    from concordance.agent.tools import ModelTools

    return {"tables": ModelTools(context.graph).list_tables()}


def table(context: ApiContext, params: Params) -> dict[str, Any]:
    from concordance.agent.tools import ModelTools

    result = ModelTools(context.graph).describe_table(_one(params, "name"))
    if "error" in result:
        raise ApiError(HTTPStatus.NOT_FOUND, result["error"], **_hint(result))
    return result


def measures(context: ApiContext, params: Params) -> dict[str, Any]:
    from concordance.agent.tools import ModelTools

    return {"measures": ModelTools(context.graph).list_measures()}


def measure(context: ApiContext, params: Params) -> dict[str, Any]:
    """One measure in full: expression, canonical form, fingerprint, both directions.

    The canonical form is included because it is the thing actually hashed. A
    fingerprint shown without it asks to be taken on trust, which is the
    opposite of the point.
    """
    from concordance.agent.tools import ModelTools
    from concordance.normalize.dax import canonicalise

    name = _one(params, "name")
    result = ModelTools(context.graph).describe_measure(name)
    if "error" in result:
        raise ApiError(HTTPStatus.NOT_FOUND, result["error"], **_hint(result))

    found = next(
        m for m in context.graph.model.measures if m.name.casefold() == result["name"].casefold()
    )
    result["canonical"] = canonicalise(found.expression)
    result["fingerprint_full"] = found.fingerprint
    return result


def impact(context: ApiContext, params: Params) -> dict[str, Any]:
    """What breaks if this object changes."""
    from concordance.agent.tools import ModelTools

    result = ModelTools(context.graph).what_uses(_one(params, "name"))
    if "error" in result:
        raise ApiError(HTTPStatus.NOT_FOUND, result["error"], **_hint(result))
    return result


def requirements(context: ApiContext, params: Params) -> dict[str, Any]:
    """Generated requirements of one kind, each bound to its evidence."""
    raw = (params.get("kind") or ["business"])[0].strip().casefold()
    try:
        kind = Kind(raw)
    except ValueError:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            f"unknown requirement kind {raw!r}",
            accepted=[k.value for k in Kind],
        ) from None

    derived = context.requirements(kind)
    return {
        "kind": kind.value,
        "model": context.graph.model.name,
        "counts": {
            "total": len(derived),
            **{
                level.value: sum(1 for r in derived if r.confidence is level)
                for level in Confidence
            },
        },
        "requirements": [_requirement_dict(r) for r in derived],
    }


def review(context: ApiContext, params: Params) -> dict[str, Any]:
    """Everything the system is not confident about, from both document kinds.

    This is a queue rather than a report: these are the statements a person has
    to accept or correct before the document can be relied on. Serving it as its
    own endpoint keeps that obligation visible instead of buried among the
    requirements that need no attention.
    """
    pending = [
        _requirement_dict(r)
        for kind in Kind
        for r in context.requirements(kind)
        if r.needs_review
    ]
    return {"model": context.graph.model.name, "count": len(pending), "pending": pending}


def drift(context: ApiContext, params: Params) -> dict[str, Any]:
    """What moved between the served model and the one configured to compare against."""
    if context.compare_to is None:
        raise ApiError(
            HTTPStatus.NOT_IMPLEMENTED,
            "no comparison model was configured; restart with --compare-to <model>",
        )

    before = snap.take(context.compare_to, label=context.compare_label or "before")
    after = snap.take(context.graph, label=context.graph.model.name)
    report = compare(before, after, after_graph=context.graph)

    return {
        "before": report.before_label,
        "after": report.after_label,
        "model": report.model_name,
        "has_drift": report.has_drift,
        "counts": report.counts(),
        "changes": [
            {
                "node_id": c.node_id,
                "kind": c.kind.value,
                "object_kind": c.object_kind,
                "summary": c.summary,
                # False only for a rename: the logic behind it is provably the
                # same, so the interface can say so rather than implying work.
                "is_semantic": c.is_semantic,
                "before": _record(c.before),
                "after": _record(c.after),
            }
            for c in report.changes
        ],
        "affected_requirements": [
            {
                "requirement": _requirement_dict(a.requirement),
                "because": [c.summary for c in a.changes],
                "needs_revalidation": a.needs_revalidation,
            }
            for a in report.affected
        ],
    }


def reconcile(context: ApiContext, params: Params) -> dict[str, Any]:
    """Whether the warehouse agrees with the model, metric by metric."""
    if context.warehouse is None:
        raise ApiError(
            HTTPStatus.NOT_IMPLEMENTED,
            "no warehouse was configured; restart with --warehouse <path.duckdb>",
        )

    import duckdb

    from concordance.adapters import sql as sqladapter
    from concordance.reconcile import metrics

    connection = duckdb.connect(str(context.warehouse), read_only=True)
    try:
        warehouse_model = sqladapter.from_duckdb(connection, schema=context.warehouse_schema)
    finally:
        connection.close()

    report = metrics.reconcile(
        metrics.from_power_bi(context.graph), metrics.from_warehouse(warehouse_model)
    )

    return {
        "model": context.graph.model.name,
        "warehouse": str(context.warehouse.name),
        "counts": report.counts(),
        "comparisons": [
            {
                "metric": c.metric,
                "verdict": c.verdict.value,
                "needs_attention": c.needs_attention,
                "definitions": [
                    {
                        "platform": d.platform,
                        "language": d.language,
                        "expression": d.expression,
                        "tables": sorted(d.tables),
                        "columns": sorted(d.columns),
                        "aggregations": sorted(d.aggregations),
                        "resolved_through": list(d.resolved_through),
                    }
                    for d in c.definitions
                ],
                "differences": [
                    {"aspect": d.aspect, "detail": d.detail} for d in c.differences
                ],
            }
            for c in report.comparisons
        ],
        "unique_to_platform": report.unique_to_platform,
        "possible_pairings": [
            {
                "left": p.left,
                "left_platform": p.left_platform,
                "right": p.right,
                "right_platform": p.right_platform,
                "similarity": p.similarity,
                "basis": p.basis,
                "contradicted": p.contradicted,
                "evidence": p.evidence,
            }
            for p in report.possible_pairings
        ],
        "coverage_gaps": [
            {"feature": g.feature, "count": g.count, "reason": g.reason}
            for g in warehouse_model.coverage_gaps
        ],
    }


def _record(record: Any) -> dict[str, Any] | None:
    """One side of a change.

    ``detail`` is whatever the fingerprint was computed over -- an expression, a
    drill path, a join label -- so showing it beside the hash is what lets a
    reader see *what* moved rather than only that something did.
    """
    if record is None:
        return None
    return {
        "node_id": record.node_id,
        "kind": record.kind,
        "fingerprint": record.fingerprint,
        "short_fingerprint": record.fingerprint[:12],
        "detail": record.detail,
    }


def _hint(result: dict[str, Any]) -> dict[str, Any]:
    """Carry a `did_you_mean` through to the error body when the tool offered one."""
    suggestions = result.get("did_you_mean")
    return {"did_you_mean": suggestions} if suggestions else {}


#: Route table. Every entry is read-only and safe to serve without a session.
ROUTES: dict[str, Callable[[ApiContext, Params], dict[str, Any]]] = {
    "/api/overview": overview,
    "/api/graph": graph,
    "/api/tables": tables,
    "/api/table": table,
    "/api/measures": measures,
    "/api/measure": measure,
    "/api/impact": impact,
    "/api/requirements": requirements,
    "/api/review": review,
    "/api/drift": drift,
    "/api/reconcile": reconcile,
}

#: Answered from the registry rather than from one model, so it is listed apart
#: from the routes above -- every one of those takes a single resolved context.
_REGISTRY_ROUTES = ("/api/models",)

ALL_ROUTES = tuple(sorted(ROUTES)) + _REGISTRY_ROUTES


def handle(
    source: ApiContext | ModelRegistry, path: str, params: Params
) -> tuple[HTTPStatus, dict[str, Any]]:
    """Run one read-only request, turning failures into a status and a message.

    Accepts a bare context as well as a registry: serving one model is the
    common case and should not have to build a registry to say so.
    """
    registry = source if isinstance(source, ModelRegistry) else ModelRegistry.of(source)

    try:
        if path == "/api/models":
            return HTTPStatus.OK, registry.describe()

        route = ROUTES.get(path)
        if route is None:
            return HTTPStatus.NOT_FOUND, {
                "error": f"no such route: {path}",
                "routes": list(ALL_ROUTES),
            }
        # Resolved inside the try so an unknown ?model= is reported the same
        # way as any other bad parameter, rather than escaping as a 500.
        return HTTPStatus.OK, route(registry.resolve(params), params)
    except ApiError as error:
        return error.status, error.payload()

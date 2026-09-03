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

from dataclasses import dataclass, replace
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
    #: True for a model a visitor uploaded through the browser rather than one
    #: an operator loaded at startup. It changes nothing about how the model is
    #: read -- every route treats the two identically, which is the point -- and
    #: exists so the interface can say which is which, and so the two
    #: capabilities that need a second file can explain that an uploaded model
    #: arrived without one.
    uploaded: bool = False
    #: Where review decisions are written. Absent means the queue is read-only,
    #: which the interface says rather than showing controls that do nothing.
    decisions: Path | None = None
    #: True when that file does not survive a restart -- a container with no
    #: mounted volume, which is what the hosted demo runs on.
    #:
    #: Declared by whoever starts the server, because nothing here can work it
    #: out: a path looks identical whether or not the filesystem under it is
    #: persistent. It exists because the alternative is silence, and silence
    #: here means somebody signs a statement off believing it was recorded.
    #: Under ALCOA+ a record has to be *enduring*; when it is not, the person
    #: making it is the one who needs to know.
    decisions_reset: bool = False
    #: The provider the chat already uses. Reused, not reconfigured, for the
    #: optional AI summary on drift and reconcile -- absent means that summary
    #: is unavailable, the same way an absent warehouse means reconcile is.
    provider: Any = None

    #: Every measure run against the model's own rows, once. Unlike
    #: `requirements` below this *is* cached, and for the opposite reason:
    #: loading 1.3 million rows into DuckDB takes about three seconds, which is
    #: fine once and unacceptable on every request. Staleness is not a risk --
    #: a model's file does not change under a running server, and an uploaded
    #: one lives only as long as the session that uploaded it.
    _evaluated: Any = None
    #: The model's own rows, in DuckDB, kept open for the life of the context.
    #: Opened once and shared by every figure and every chart, because the
    #: three seconds is in the loading and not in the querying: the KPI row and
    #: the four charts under it are dozens of queries against one load.
    _connection: Any = None
    _rows: int = 0
    _data_reason: str = ""
    _opened: bool = False

    def requirements(self, kind: Kind) -> list[Requirement]:
        """Derive requirements on demand.

        Not cached: derivation is fast, and a stale cache is a worse failure
        than a repeated computation for a tool whose whole claim is that the
        document matches the model.
        """
        return [r for r in RequirementDeriver(self.graph).derive() if r.kind is kind]

    def data(self):
        """The model's rows, loaded once. ``(connection, rows, reason)``.

        ``connection`` is ``None`` when the source carries no rows -- a
        `.SemanticModel` folder is a schema -- and ``reason`` then says so in
        words meant for a reader rather than a log.
        """
        from concordance.generate.evaluate import open_data

        if not self._opened:
            self._opened = True
            self._connection, self._rows, self._data_reason = open_data(
                self.graph.model
            )
        return self._connection, self._rows, self._data_reason

    def evaluated(self):
        """The model's measures, run against its own data. Computed once."""
        from concordance.generate.evaluate import Evaluation, evaluate

        if self._evaluated is None:
            connection, rows, reason = self.data()
            if connection is None:
                self._evaluated = Evaluation(available=False, reason=reason)
            else:
                run = evaluate(self.graph.model, connection=connection)
                self._evaluated = replace(run, rows_loaded=rows)
        return self._evaluated


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

    def plus(self, extra: dict[str, ApiContext]) -> ModelRegistry:
        """This registry with some more models visible, for one request only.

        What makes uploads possible without touching a single route. A model a
        visitor uploaded is not in the registry the server started with -- it
        must not be, or it would be visible to every other visitor -- so it is
        layered on for the duration of the request that asked, and every
        endpoint resolves it exactly as it resolves a configured one.

        The default is unchanged: an upload becomes available, never automatic.
        Somebody opening the page in another tab still lands on the model this
        server was started for.
        """
        if not extra:
            return self
        return ModelRegistry(contexts={**self.contexts, **extra}, default=self.default)

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
                    "uploaded": context.uploaded,
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
    log = _decision_log(context)
    pending = []
    for kind in Kind:
        for requirement in context.requirements(kind):
            if not requirement.needs_review:
                continue
            entry = _requirement_dict(requirement)
            entry["standing"] = _standing_dict(log, requirement)
            pending.append(entry)

    # Counted separately rather than by filtering in the interface: "seven
    # open, two decided, one stale" is the shape of the queue, and a caller
    # that has to derive it will derive it differently in each place.
    def count(status: str) -> int:
        return sum(1 for p in pending if p["standing"]["status"] == status)

    return {
        "model": context.graph.model.name,
        "count": len(pending),
        "open": count("open"),
        "decided": count("decided"),
        "stale": count("stale"),
        "can_decide": context.decisions is not None,
        # Only meaningful when the queue is writable at all; the view reads it
        # alongside can_decide rather than on its own.
        "decisions_reset": context.decisions_reset,
        # So the view can say *why* the queue is read-only. An uploaded model
        # has no log by design rather than by omission, and telling someone to
        # restart with --decisions would send them to fix a flag that is
        # already set.
        "uploaded": context.uploaded,
        "pending": pending,
    }


def _decision_log(context: ApiContext):
    """The log for this context, or None when none was configured.

    Reopened per request rather than held: several people may have the queue
    open, and a handle read once at startup would show each of them a trail
    frozen before the others' decisions.
    """
    if context.decisions is None:
        return None
    from concordance.review.decisions import DecisionLog

    return DecisionLog.open(context.decisions)


def _standing_dict(log, requirement: Requirement) -> dict[str, Any]:
    """Where one requirement stands, including why a decision stopped applying."""
    if log is None:
        return {"status": "open", "verdict": "", "history": []}

    standing = log.standing(requirement.id, requirement.bound_fingerprints)
    return {
        "status": standing.status.value,
        "verdict": standing.verdict,
        "note": standing.latest.note if standing.latest else "",
        "author_claimed": standing.latest.author if standing.latest else "",
        "author_verified": (
            standing.latest.author_verified if standing.latest else False
        ),
        "at": standing.latest.at if standing.latest else "",
        "history": [
            {
                "verdict": d.verdict.value,
                "note": d.note,
                "author_claimed": d.author,
                "author_verified": d.author_verified,
                "at": d.at,
            }
            for d in standing.history
        ],
    }


def decide(
    context: ApiContext, payload: dict[str, Any], author: str = ""
) -> dict[str, Any]:
    """Record one person's answer about one requirement.

    The fingerprints written down are the ones derived *now*, not any supplied
    by the caller. A client that could state what it was deciding about could
    accept a statement while recording that it had approved something else --
    which is the one thing this record exists to make impossible.

    ``author`` is passed in by the handler when it resolved the request's token
    to a person, and it wins over anything in the body for the same reason:
    a caller able to name the author could sign off as a colleague.
    """
    from concordance.review.decisions import DecisionLog, Verdict

    if context.decisions is None:
        # Two different reasons, and telling someone the wrong one sends them
        # to fix a flag that is already set. An uploaded model has no log
        # because it has no future: it lives in one browser session, its
        # requirement ids are unique only within itself, and a signature
        # recorded against it would outlive the thing it was about.
        raise ApiError(
            HTTPStatus.NOT_IMPLEMENTED,
            "an uploaded model cannot be signed off: it is held for this "
            "browser session only, so a decision recorded against it would "
            "outlast the model it was about. Load it with `concordance serve` "
            "to review it for real."
            if context.uploaded
            else "this server was started without a decision log; "
            "restart with --decisions <path.jsonl> to record review outcomes",
        )

    requirement_id = str(payload.get("requirement_id", "")).strip()
    if not requirement_id:
        raise ApiError(HTTPStatus.BAD_REQUEST, "requirement_id is required")

    try:
        verdict = Verdict(str(payload.get("verdict", "")).strip())
    except ValueError:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            "verdict must be one of: " + ", ".join(v.value for v in Verdict),
        ) from None

    # A rejection or a correction is a change to the record, and a change to a
    # record has to say why. 21 CFR Part 11 -- which is the rule this log exists
    # under, in a pharmaceutical company -- requires an audit trail to capture
    # who, what, when *and why*. The interface already insisted on a reason; the
    # API did not, so a decision recorded any other way went in with an empty
    # note and the trail lost the one field a later reader most needs.
    #
    # Not required for an acceptance: "the statement stands as written" is the
    # reason, and demanding prose for it would train reviewers to type "ok".
    note = str(payload.get("note", "") or "").strip()
    if len(note) > _MAX_NOTE:
        raise ApiError(
            HTTPStatus.BAD_REQUEST, f"note must be under {_MAX_NOTE} characters"
        )
    if verdict in (Verdict.REJECTED, Verdict.CORRECTED) and not note:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            f"a {verdict.value} decision must say why: the audit trail records "
            f"who decided, what they decided, when, and the reason, and the "
            f"reason is the part the next person reads instead of starting the "
            f"investigation again",
        )

    found = next(
        (
            r
            for kind in Kind
            for r in context.requirements(kind)
            if r.id == requirement_id
        ),
        None,
    )
    if found is None:
        raise ApiError(
            HTTPStatus.NOT_FOUND, "no requirement with that id in this model"
        )

    log = DecisionLog.open(context.decisions)
    log.record(
        requirement_id=found.id,
        verdict=verdict,
        bound_fingerprints=found.bound_fingerprints,
        note=note,
        author=author or str(payload.get("author", ""))[:_MAX_AUTHOR],
        author_verified=bool(author),
    )
    return {"requirement_id": found.id, "standing": _standing_dict(log, found)}


#: A review note is a sentence or two of context, not a document.
_MAX_NOTE = 2000
_MAX_AUTHOR = 120


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

    result = {
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
    if _wants_summary(params):
        result["summary"] = _narrate(context, "drift", result)
    return result


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

    try:
        connection = duckdb.connect(str(context.warehouse), read_only=True)
    except duckdb.Error as error:
        # The file at --warehouse changed underneath a running server -- moved,
        # truncated, replaced with something that isn't a database at all. Left
        # unguarded this reaches the request thread as a raw IOException, which
        # the client sees as a dropped connection rather than an answer: no
        # status, no body, nothing to act on. A 502 here is deliberate --
        # nothing about this request into Concordance was wrong; the warehouse
        # it depends on is what failed to open.
        raise ApiError(
            HTTPStatus.BAD_GATEWAY,
            f"cannot open the warehouse at {context.warehouse}: {error}",
        ) from error
    try:
        warehouse_model = sqladapter.from_duckdb(connection, schema=context.warehouse_schema)
    finally:
        connection.close()

    report = metrics.reconcile(
        metrics.from_power_bi(context.graph), metrics.from_warehouse(warehouse_model)
    )

    result = {
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
    if _wants_summary(params):
        result["summary"] = _narrate(context, "reconcile", result)
    return result


def _wants_summary(params: Params) -> bool:
    """Opt-in only: a summary spends LLM quota and latency the caller may not want."""
    return (params.get("summary") or [""])[0].strip().lower() in ("1", "true", "yes")


def _narrate(context: ApiContext, kind: str, result: dict[str, Any]) -> dict[str, Any]:
    """Best-effort AI summary. Never fails the request it rides along with.

    A missing key, an exhausted quota, or a network hiccup is exactly as
    common here as it is for the chat, and the drift or reconcile report
    underneath is the actual answer -- it must still be returned in full.
    """
    from concordance.generate import narrative

    if context.provider is None:
        return {"text": None, "error": "no language model provider is configured"}
    try:
        if kind == "drift":
            found = narrative.summarize_drift(result, context.provider)
        else:
            found = narrative.summarize_reconcile(result, context.provider)
    except narrative.LlmError as error:
        return {"text": None, "error": str(error)}
    return {"text": found.text, "provider": found.provider, "disclaimer": found.disclaimer}


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


def report(context: ApiContext, params: Params) -> dict[str, Any]:
    """The dashboard's tiles, each joined to the DAX and SQL behind it.

    The one question the reviewers said was missing: given a tile called "Total
    Sales", which measure produces that number and what query would reproduce
    it. Answered only for a source that carries a report -- a `.SemanticModel`
    folder is the model alone, and the empty answer here is a true one rather
    than a failure.
    """
    from concordance.generate.sql import DIALECTS
    from concordance.generate.tiles import correlate, counts

    grain = tuple(g for g in (params.get("grain") or []) if g.strip())
    dialect = _one(params, "dialect", required=False).lower() or "duckdb"
    if dialect not in DIALECTS:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            f"unknown dialect {dialect!r}; choose one of " + ", ".join(sorted(DIALECTS)),
        )

    pages = correlate(context.graph, grain, dialect)
    return {
        "model": context.graph.model.name,
        "source_format": context.graph.model.source_type,
        "dialect": dialect,
        "grain": list(grain),
        "counts": counts(pages),
        "pages": [
            {
                "name": page.name,
                "ordinal": page.ordinal,
                "tiles": [
                    {
                        "title": tile.title,
                        "visual_type": tile.visual_type,
                        # "what is the KPI and non-KPI as a part of your DAX"
                        "is_kpi": tile.is_kpi,
                        "fields": [
                            {
                                "role": field.role,
                                "table": field.table,
                                "name": field.name,
                                "qualified_name": field.qualified_name,
                                "aggregation": field.aggregation,
                                "kind": field.kind,
                                "dax": field.expression,
                                "sql": field.sql,
                                "reason": field.reason,
                            }
                            for field in tile.fields
                        ],
                    }
                    for tile in page.tiles
                ],
            }
            for page in pages
        ],
    }


def dataset(context: ApiContext, params: Params) -> dict[str, Any]:
    """Every measure in one model, with its DAX and its SQL side by side.

    Exists because reading a model one measure at a time is the wrong shape for
    the question people actually arrive with -- "what does this dataset
    compute, and how would I get the same numbers myself". Answering that used
    to mean opening each measure in turn and copying it out.

    ``grain`` is the SQL side's filter context, written down. Passing none asks
    for the whole-model figure, which is a single row.
    """
    from concordance.generate.sql import (
        DIALECTS,
        combine,
        Status,
        joins,
        to_dialect,
        translate_all,
    )

    model = context.graph.model
    grain = tuple(g for g in (params.get("grain") or []) if g.strip())
    dialect = _one(params, "dialect", required=False).lower() or "duckdb"
    if dialect not in DIALECTS:
        raise ApiError(
            HTTPStatus.BAD_REQUEST,
            f"unknown dialect {dialect!r}; choose one of "
            + ", ".join(sorted(DIALECTS)),
        )

    # Every measure as columns of as few queries as possible. Asked for twice:
    # "convert the whole thing into an SQL query rather than giving one each".
    queries, not_combined = combine(model, grain, dialect)

    rows: list[dict[str, Any]] = []
    for translation in translate_all(model, grain):
        measure = next(
            (m for m in model.measures if m.name == translation.measure), None
        )
        rows.append(
            {
                "measure": translation.measure,
                "table": getattr(measure, "table", ""),
                "folder": getattr(measure, "display_folder", "") or "",
                "description": getattr(measure, "description", "") or "",
                "dax": getattr(measure, "expression", ""),
                "sql": to_dialect(translation.sql, dialect) if translation.sql else "",
                "status": translation.status.value,
                "reason": translation.reason,
                "blocked_by": translation.blocked_by,
                "reads_tables": sorted(translation.reads_tables),
            }
        )

    translated = sum(1 for r in rows if r["status"] == Status.EXACT.value)
    return {
        "model": model.name,
        "grain": list(grain),
        "dialect": dialect,
        # The tables and how they join, alongside the measures rather than on a
        # separate page. Asked for as one question -- "what are the data sets
        # and how it is joined with each other and what are the SQL" -- and
        # answering it in three places is how someone ends up reading the
        # measures without ever seeing what a JOIN in them refers to.
        "tables": [
            {
                "name": t.name,
                "columns": sum(1 for c in model.columns if c.table == t.name),
                "measures": sum(1 for m in model.measures if m.table == t.name),
                # A container holding only measures is a grouping, not a data
                # entity -- "Analysis DAX" in the Microsoft sample holds 40
                # measures and no columns. Saying so stops a reader looking for
                # the table it joins to, which is none.
                "measures_only": t.is_measure_only,
                # A calculated table stores nothing: both its rows and its
                # columns come from this DAX. Saying so stops a reader looking
                # for the source query it loads from, which is none, and it is
                # the only definition its columns have.
                "dax": t.dax_expression,
            }
            for t in sorted(model.user_tables(), key=lambda t: t.name)
        ],
        "joins": [
            {
                "from_table": j.from_table,
                "from_column": j.from_column,
                "to_table": j.to_table,
                "to_column": j.to_column,
                "cardinality": j.cardinality,
                "cross_filter": j.cross_filter,
                "active": j.active,
                "sql": j.sql,
            }
            for j in joins(model, dialect)
        ],
        "grain_options": _grain_options(model),
        "dialects": sorted(DIALECTS),
        "counts": {
            "measures": len(rows),
            "translated": translated,
            "blocked": len(rows) - translated,
        },
        "measures": rows,
        "combined": [
            {"label": q.label, "sql": q.sql, "measures": list(q.measures)}
            for q in queries
        ],
        "not_combined": [
            {"measure": name, "reason": reason} for name, reason in not_combined
        ],
    }


def _grain_options(model) -> list[dict[str, str]]:
    """Columns worth grouping by, so the caller need not know the schema.

    Offered from dimension tables only: the ones other tables point at, which
    point at nothing themselves. That leaf test is what separates Site and
    Calendar from Batch -- Batch is pointed at by TestResult but also points at
    Product, Site and Calendar, which makes it a fact table. Grouping a fact
    table by one of its own measures-in-waiting ("yield per yield") is legal
    SQL and never the question, and offering every column in the model would
    bury the handful that are.
    """
    referenced = {r.to_table for r in model.relationships}
    references = {r.from_table for r in model.relationships}
    dimensions = referenced - references
    keys = {(r.to_table, r.to_column) for r in model.relationships}
    options: list[dict[str, str]] = []
    for column in model.columns:
        if column.table not in dimensions:
            continue
        if (column.table, column.name) in keys:
            continue  # a join key groups by an opaque id
        if (getattr(column, "expression", "") or "").strip():
            continue  # calculated, so not present in the source
        options.append(
            {
                "value": f"{column.table}[{column.name}]",
                "table": column.table,
                "column": column.name,
            }
        )
    return sorted(options, key=lambda o: (o["table"], o["column"]))


def _hint(result: dict[str, Any]) -> dict[str, Any]:
    """Carry a `did_you_mean` through to the error body when the tool offered one."""
    suggestions = result.get("did_you_mean")
    return {"did_you_mean": suggestions} if suggestions else {}


#: Route table. Every entry is read-only and safe to serve without a session.
def find(context: ApiContext, params: Params) -> dict[str, Any]:
    """Everything in the model whose name matches, in one request.

    One endpoint rather than the caller fetching five payloads and joining them
    in the browser: the answer is small, the model is already in memory here,
    and a search that has to wait on the dataset page loading is a search
    nobody uses.
    """
    from concordance.web.search import search

    return search(context.graph, (params.get("q") or [""])[0])


def values(context: ApiContext, params: Params) -> dict[str, Any]:
    """What each measure actually comes to, run against the model's own rows.

    The only endpoint that returns a figure this project computed rather than
    read. Each one travels with the query that produced it, so a reader can
    check it instead of trusting it, and a measure that does not translate
    returns no figure at all rather than a stand-in zero.
    """
    run = context.evaluated()
    return {
        "model": context.graph.model.name,
        "available": run.available,
        "reason": run.reason,
        "rows": run.rows_loaded,
        "values": [
            {
                "measure": value.measure,
                "table": value.table,
                "value": value.value,
                "sql": value.sql,
                "reason": value.reason,
            }
            for value in run.values
        ],
    }


def dashboard(context: ApiContext, params: Params) -> dict[str, Any]:
    """One measure, split every way the model can honestly split it.

    A KPI card answers "what is the total"; this answers "what is it made of",
    from the same rows and with the same SQL, so a bar is exactly as checkable
    as the figure above it. Which splits exist is decided by measuring the data
    -- see `generate/breakdown.py` -- so a model with nothing chartable says so
    rather than drawing an empty grid.
    """
    from concordance.generate.breakdown import build

    model = context.graph.model
    wanted = str(params.get("measure", "")).strip()
    if not wanted:
        # The model's own declaration order, not the translator's: the first
        # measure an author wrote is a defensible default, and the first one
        # that happens to translate is alphabetical accident.
        computed = {v.measure for v in context.evaluated().values if v.computed}
        wanted = next(
            (m.name for m in model.measures if m.name in computed),
            model.measures[0].name if model.measures else "",
        )

    connection, _rows, reason = context.data()
    if connection is None:
        return {
            "model": model.name,
            "measure": wanted,
            "available": False,
            "reason": reason,
            "breakdowns": [],
            "dimensions": [],
        }

    built = build(model, connection, wanted)
    return {
        "model": model.name,
        "measure": built.measure,
        "available": built.available,
        "reason": built.reason,
        "breakdowns": [
            {
                "by": b.by,
                "table": b.table,
                "column": b.column,
                "total": b.total,
                "folded": b.folded,
                "sql": b.sql,
                "slices": [{"label": s.label, "value": s.value} for s in b.slices],
            }
            for b in built.breakdowns
        ],
        "dimensions": [dict(d) for d in built.dimensions],
    }


ROUTES: dict[str, Callable[[ApiContext, Params], dict[str, Any]]] = {
    "/api/overview": overview,
    "/api/search": find,
    "/api/values": values,
    "/api/dashboard": dashboard,
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
    "/api/dataset": dataset,
    "/api/report": report,
}

#: Answered outside `ROUTES` because neither is a pure function of a context:
#: `/api/models` is answered from the registry, and `/api/whoami` from the
#: request's own token. Listed so the 404 body still names every real route.
_REGISTRY_ROUTES = ("/api/models", "/api/whoami")

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

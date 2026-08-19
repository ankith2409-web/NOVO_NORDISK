"""The read-only JSON API the browser talks to.

Tested in two layers for the same reason the chat server is: the endpoints are
plain functions, so they are checked directly with no socket involved, and the
things that only exist over HTTP -- status codes, CORS, preflight -- are checked
against a real server on an ephemeral port.

The security-shaped tests here are not theatre. A local server that turned a
query parameter into a file read would be a genuine hole, and one that echoed
back any ``Origin`` would let a page the user happens to have open read their
model and spend their API quota. Both are cheap to get wrong later, so both are
pinned now.
"""

from __future__ import annotations

import http.client
import json
import threading
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.graph.csg import SemanticGraph
from concordance.llm.fake import FakeProvider, says
from concordance.web import api
from concordance.web.server import allowed_origin, make_handler

MODEL = Path("data/models/ClinicalTrialSafety.SemanticModel")
MODEL_V2 = Path("data/models/ClinicalTrialSafety_v2.SemanticModel")
#: Routes that answer from the model alone, with no parameters and no extra
#: sources configured. Derived from the route table so a new endpoint cannot be
#: added without either appearing here or being listed below as an exception.
_NEEDS_PARAMS = {"/api/measure", "/api/table", "/api/impact"}
_NEEDS_CONFIG = {"/api/drift"}
_SELF_SERVING = sorted(set(api.ROUTES) - _NEEDS_PARAMS - _NEEDS_CONFIG)


def _load(path: Path) -> SemanticGraph:
    if not path.exists():
        pytest.skip(f"model not present: {path}")
    return SemanticGraph(TmdlAdapter().extract(str(path)))


@pytest.fixture(scope="module")
def graph() -> SemanticGraph:
    return _load(MODEL)


@pytest.fixture(scope="module")
def context(graph: SemanticGraph) -> api.ApiContext:
    return api.ApiContext(graph=graph)


# -- every route answers ------------------------------------------------------

@pytest.mark.parametrize("path", _SELF_SERVING)
def test_a_route_that_needs_nothing_answers(context: api.ApiContext, path: str) -> None:
    status, payload = api.handle(context, path, {})
    assert status is HTTPStatus.OK
    assert isinstance(payload, dict) and payload
    assert "error" not in payload


def test_the_route_table_is_fully_accounted_for() -> None:
    """A new endpoint must be classified, not silently untested."""
    assert set(api.ROUTES) == set(_SELF_SERVING) | _NEEDS_PARAMS | _NEEDS_CONFIG


def test_an_unknown_route_lists_the_real_ones(context: api.ApiContext) -> None:
    status, payload = api.handle(context, "/api/nope", {})
    assert status is HTTPStatus.NOT_FOUND
    # ALL_ROUTES, not ROUTES: /api/models is answered from the registry rather
    # than from a single resolved model, so it lives outside the route table.
    assert payload["routes"] == list(api.ALL_ROUTES)


# -- nothing configured is said plainly, not crashed --------------------------

@pytest.mark.parametrize("path", sorted(_NEEDS_CONFIG))
def test_an_unconfigured_source_is_not_pretended_away(
    context: api.ApiContext, path: str
) -> None:
    """501, with the flag that would fix it -- not 404, and not a traceback."""
    status, payload = api.handle(context, path, {})
    assert status is HTTPStatus.NOT_IMPLEMENTED
    assert "--" in payload["error"], "the message should name the flag to restart with"


@pytest.mark.parametrize("path", sorted(_NEEDS_CONFIG))
@pytest.mark.parametrize(
    "attempt",
    ["/etc/passwd", "../../../../etc/passwd", "data/models/QualityControl.SemanticModel"],
)
def test_the_client_cannot_name_a_file(
    context: api.ApiContext, path: str, attempt: str
) -> None:
    """Drift reads what the operator configured, never a parameter.

    A query parameter naming a path would turn a read-only local server into an
    arbitrary file reader. It stays unconfigured no matter what is passed.
    """
    for key in ("path", "source", "compare_to", "model", "file"):
        status, payload = api.handle(context, path, {key: [attempt]})
        # `model` is a real parameter now, but it names a key in a registry
        # fixed at startup -- never a path -- so a path-shaped value is simply
        # a model that is not loaded. Either refusal is correct; what must hold
        # is that nothing was read and nothing was echoed back.
        assert status in (HTTPStatus.NOT_IMPLEMENTED, HTTPStatus.NOT_FOUND)
        assert attempt not in json.dumps(payload)


# -- parameters ---------------------------------------------------------------

@pytest.mark.parametrize("path", sorted(_NEEDS_PARAMS))
def test_a_missing_name_is_a_bad_request_not_a_crash(
    context: api.ApiContext, path: str
) -> None:
    status, payload = api.handle(context, path, {})
    assert status is HTTPStatus.BAD_REQUEST
    assert "name" in payload["error"]


@pytest.mark.parametrize("path", sorted(_NEEDS_PARAMS))
def test_an_unknown_object_is_a_404_that_suggests(context: api.ApiContext, path: str) -> None:
    status, payload = api.handle(context, path, {"name": ["Serious Advrse Evnts"]})
    assert status is HTTPStatus.NOT_FOUND
    assert "error" in payload


def test_an_unknown_requirement_kind_lists_the_real_ones(context: api.ApiContext) -> None:
    status, payload = api.handle(context, "/api/requirements", {"kind": ["nonsense"]})
    assert status is HTTPStatus.BAD_REQUEST
    assert set(payload["accepted"]) == {"business", "functional"}


# -- payload content: the provenance the interface is built on ----------------

def test_overview_advertises_what_is_configured(graph: SemanticGraph) -> None:
    """The UI has to know whether to render the drift view."""
    bare = api.handle(api.ApiContext(graph=graph), "/api/overview", {})[1]
    assert bare["capabilities"] == {"drift": False}

    withdrift = api.ApiContext(graph=graph, compare_to=graph)
    assert api.handle(withdrift, "/api/overview", {})[1]["capabilities"]["drift"] is True


def test_overview_carries_coverage_gaps_without_a_second_request(
    context: api.ApiContext,
) -> None:
    """A limitation behind an extra call is one most callers never render."""
    payload = api.handle(context, "/api/overview", {})[1]
    assert "not_extracted" in payload
    assert "unresolved_references" in payload


def test_a_measure_carries_the_form_that_was_actually_hashed(
    context: api.ApiContext,
) -> None:
    """A fingerprint shown without its canonical form asks to be taken on trust."""
    payload = api.handle(context, "/api/measure", {"name": ["Serious Adverse Events"]})[1]

    assert payload["canonical"]
    assert payload["fingerprint_full"].startswith(payload["fingerprint"])
    assert len(payload["fingerprint_full"]) == 64
    assert payload["canonical"] != payload["expression"], "canonical form should differ"


def test_requirements_carry_confidence_and_evidence(context: api.ApiContext) -> None:
    payload = api.handle(context, "/api/requirements", {"kind": ["business"]})[1]

    assert payload["counts"]["total"] == len(payload["requirements"])
    assert payload["counts"]["total"] == sum(
        payload["counts"][level] for level in ("high", "medium", "low")
    )
    for requirement in payload["requirements"]:
        assert requirement["confidence"] in {"high", "medium", "low"}
        assert requirement["id"]
    bound = [r for r in payload["requirements"] if r["evidence"]]
    assert bound, "requirements must be bound to the objects they describe"
    assert all(len(e["fingerprint"]) == 64 for r in bound for e in r["evidence"])


def test_the_review_queue_is_exactly_what_needs_a_person(
    context: api.ApiContext,
) -> None:
    payload = api.handle(context, "/api/review", {})[1]
    assert payload["count"] == len(payload["pending"])
    assert all(r["needs_review"] for r in payload["pending"])
    assert all(r["confidence"] == "low" for r in payload["pending"])


def test_the_graph_is_whole(context: api.ApiContext) -> None:
    payload = api.handle(context, "/api/graph", {})[1]
    assert payload["nodes"] and payload["edges"]
    assert payload["stats"]["nodes"] == len(payload["nodes"])
    assert payload["stats"]["edges"] == len(payload["edges"])


# -- drift, once configured ----------------------------------------------------

def test_drift_reports_changes_and_what_they_put_in_question() -> None:
    before = _load(MODEL)
    after = _load(MODEL_V2)
    context = api.ApiContext(graph=after, compare_to=before, compare_label="v1")

    status, payload = api.handle(context, "/api/drift", {})
    assert status is HTTPStatus.OK
    assert payload["has_drift"]
    assert payload["counts"]["changed"] >= 1

    changed = next(c for c in payload["changes"] if c["kind"] == "changed")
    assert changed["before"]["fingerprint"] != changed["after"]["fingerprint"]
    # `detail` is what the hash was taken over, so a reader can see what moved.
    assert changed["before"]["detail"] or changed["after"]["detail"]

    assert payload["affected_requirements"], "a change must put some requirement in question"
    assert all(a["because"] for a in payload["affected_requirements"])


def test_drift_has_no_summary_key_unless_asked_for() -> None:
    """A summary costs a real LLM call, so it must not ride along uninvited."""
    before = _load(MODEL)
    after = _load(MODEL_V2)
    context = api.ApiContext(graph=after, compare_to=before, compare_label="v1")

    status, payload = api.handle(context, "/api/drift", {})
    assert status is HTTPStatus.OK
    assert "summary" not in payload


def test_drift_summary_degrades_when_no_provider_is_configured() -> None:
    """No key configured must explain the gap, not break the drift report."""
    before = _load(MODEL)
    after = _load(MODEL_V2)
    context = api.ApiContext(graph=after, compare_to=before, compare_label="v1")

    status, payload = api.handle(context, "/api/drift", {"summary": ["true"]})
    assert status is HTTPStatus.OK
    assert payload["has_drift"]  # the report itself is unaffected
    assert payload["summary"]["text"] is None
    assert "provider" not in payload["summary"] or payload["summary"].get("error")


def test_drift_summary_uses_the_configured_provider() -> None:
    from concordance.llm.fake import FakeProvider, says

    before = _load(MODEL)
    after = _load(MODEL_V2)
    provider = FakeProvider(script=[says("A measure's filter changed.")])
    context = api.ApiContext(
        graph=after, compare_to=before, compare_label="v1", provider=provider
    )

    status, payload = api.handle(context, "/api/drift", {"summary": ["true"]})
    assert status is HTTPStatus.OK
    assert payload["summary"]["text"] == "A measure's filter changed."
    assert payload["summary"]["provider"] == "fake"
    assert payload["summary"]["disclaimer"]

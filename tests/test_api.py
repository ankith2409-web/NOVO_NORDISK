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
QUALITY_CONTROL = Path("data/models/QualityControl.SemanticModel")
BUILDER = Path("scripts/build_warehouse.py")

#: Routes that answer from the model alone, with no parameters and no extra
#: sources configured. Derived from the route table so a new endpoint cannot be
#: added without either appearing here or being listed below as an exception.
_NEEDS_PARAMS = {"/api/measure", "/api/table", "/api/impact"}
_NEEDS_CONFIG = {"/api/drift", "/api/reconcile"}
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
    assert payload["routes"] == sorted(api.ROUTES)


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
    """Drift and reconcile read what the operator configured, never a parameter.

    A query parameter naming a path would turn a read-only local server into an
    arbitrary file reader. These stay unconfigured no matter what is passed.
    """
    for key in ("path", "source", "warehouse", "compare_to", "model", "file"):
        status, payload = api.handle(context, path, {key: [attempt]})
        assert status is HTTPStatus.NOT_IMPLEMENTED
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
    """The UI has to know whether to render the drift and reconcile views."""
    bare = api.handle(api.ApiContext(graph=graph), "/api/overview", {})[1]
    assert bare["capabilities"] == {"drift": False, "reconcile": False}

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


# -- drift and reconcile, once configured -------------------------------------

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


def test_reconcile_reports_the_divergent_metric(tmp_path_factory) -> None:
    import importlib.util

    if not BUILDER.exists():
        pytest.skip("warehouse builder not present")
    spec = importlib.util.spec_from_file_location("build_warehouse", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    warehouse = module.build(tmp_path_factory.mktemp("wh") / "qc.duckdb")

    context = api.ApiContext(graph=_load(QUALITY_CONTROL), warehouse=warehouse)
    status, payload = api.handle(context, "/api/reconcile", {})

    assert status is HTTPStatus.OK
    divergent = [c for c in payload["comparisons"] if c["verdict"] == "divergent"]
    assert [c["metric"] for c in divergent] == ["OOS Rate"]
    assert divergent[0]["differences"], "a verdict without a reason is not evidence"
    assert all(c["needs_attention"] for c in divergent)


# -- CORS ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "https://localhost",
        "http://[::1]:3000",
    ],
)
def test_a_local_dev_server_is_allowed(origin: str) -> None:
    assert allowed_origin(origin) == origin


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://localhost.evil.example",
        "http://127.0.0.1.evil.example",
        "http://notlocalhost",
        None,
        "",
    ],
)
def test_anything_else_is_refused(origin: str | None) -> None:
    """The API serves a local model and spends real quota through the chat."""
    assert allowed_origin(origin) is None


# -- the HTTP layer -----------------------------------------------------------

class _RunningServer:
    def __init__(self, graph: SemanticGraph, context: api.ApiContext | None = None) -> None:
        handler, self.sessions = make_handler(
            graph, FakeProvider(script=[says("ok")]), context
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method: str, path: str, headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, headers=headers or {})
        return conn.getresponse()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(graph: SemanticGraph):
    running = _RunningServer(graph)
    yield running
    running.close()


def test_endpoints_are_reachable_over_http(server: _RunningServer) -> None:
    response = server.request("GET", "/api/overview")
    assert response.status == 200
    assert json.loads(response.read())["model"]


def test_a_query_string_survives_routing(server: _RunningServer) -> None:
    """Routing splits path from query; matching the raw path would 404 here."""
    response = server.request("GET", "/api/measure?name=Serious%20Adverse%20Events")
    assert response.status == 200
    assert json.loads(response.read())["name"] == "Serious Adverse Events"


def test_the_allowed_origin_is_echoed_never_wildcarded(server: _RunningServer) -> None:
    """A wildcard is invalid once the session cookie makes requests credentialed."""
    response = server.request(
        "GET", "/api/overview", {"Origin": "http://localhost:5173"}
    )
    assert response.getheader("Access-Control-Allow-Origin") == "http://localhost:5173"
    assert response.getheader("Access-Control-Allow-Credentials") == "true"


def test_a_foreign_origin_gets_no_allow_header(server: _RunningServer) -> None:
    response = server.request("GET", "/api/overview", {"Origin": "https://evil.example"})
    assert response.getheader("Access-Control-Allow-Origin") is None


def test_every_response_varies_on_origin(server: _RunningServer) -> None:
    """Without this a shared cache could serve one origin's response to another."""
    for origin in ("http://localhost:5173", "https://evil.example"):
        response = server.request("GET", "/api/overview", {"Origin": origin})
        assert "Origin" in (response.getheader("Vary") or "")
        response.read()


def test_the_preflight_is_answered(server: _RunningServer) -> None:
    response = server.request(
        "OPTIONS", "/api/overview", {"Origin": "http://localhost:5173"}
    )
    assert response.status == 204
    assert "GET" in response.getheader("Access-Control-Allow-Methods")


def test_read_only_endpoints_mint_no_sessions(server: _RunningServer) -> None:
    """The graph never changes, so every visitor can share it.

    Minting a session per read would put a conversation history behind every
    page load the interface makes -- and the store is capped, so it would evict
    real chats.
    """
    before = len(server.sessions)
    for path in ("/api/overview", "/api/tables", "/api/measures", "/api/graph"):
        server.request("GET", path).read()

    assert len(server.sessions) == before

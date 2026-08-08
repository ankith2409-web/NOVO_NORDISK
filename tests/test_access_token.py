"""Optional access control on the local server.

Off by default, and that default is deliberate rather than an oversight: the
server binds to loopback and the overwhelmingly common use is one person on
their own machine, where a login is friction protecting nothing.

It exists because that stops being true the moment ``--host`` is opened, a
tunnel is pointed at the port, or the machine is shared -- at which point the
server is handing out a company's DAX logic and letting anyone who finds it
spend the API quota behind the chat. A shared token is not identity and is not
sold as such; it is the smallest honest control for that situation.

The test that matters most is the last one: every route has to be covered. An
auth check that guards the page but not the API is worse than none, because it
looks protected.
"""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.graph.csg import SemanticGraph
from concordance.llm.fake import FakeProvider, says
from concordance.web import api
from concordance.web.server import make_handler

MODEL = Path("data/models/QualityControl.SemanticModel")
TOKEN = "s3cret-token-value"


@pytest.fixture(scope="module")
def graph() -> SemanticGraph:
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return SemanticGraph(TmdlAdapter().extract(str(MODEL)))


class Running:
    def __init__(self, graph: SemanticGraph, token: str) -> None:
        handler, self.sessions = make_handler(
            graph, FakeProvider(script=[says("ok")]), access_token=token
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def get(self, path: str, headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path, headers=headers or {})
        return conn.getresponse()

    def post(self, path: str, body: dict, headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        merged = {"Content-Type": "application/json", **(headers or {})}
        conn.request("POST", path, body=json.dumps(body), headers=merged)
        return conn.getresponse()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def guarded(graph: SemanticGraph):
    server = Running(graph, TOKEN)
    yield server
    server.close()


@pytest.fixture
def open_server(graph: SemanticGraph):
    server = Running(graph, "")
    yield server
    server.close()


# -- off by default -----------------------------------------------------------

def test_no_token_configured_means_no_token_required(open_server: Running) -> None:
    """Local use must not change. This is the default path for every demo."""
    assert open_server.get("/api/overview").status == 200
    assert open_server.get("/").status == 200


# -- refusing ------------------------------------------------------------------

def test_a_request_without_the_token_is_refused(guarded: Running) -> None:
    response = guarded.get("/api/overview")
    assert response.status == 401
    body = json.loads(response.read())
    assert "access token" in body["error"]
    # The refusal has to say how to comply, or it is just a wall.
    assert "Authorization" in body["how"]


def test_a_wrong_token_is_refused(guarded: Running) -> None:
    response = guarded.get("/api/overview?token=not-the-right-one")
    assert response.status == 401


def test_an_empty_token_is_refused(guarded: Running) -> None:
    assert guarded.get("/api/overview?token=").status == 401


# -- accepting -----------------------------------------------------------------

def test_a_bearer_header_is_accepted(guarded: Running) -> None:
    response = guarded.get(
        "/api/overview", {"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status == 200
    assert json.loads(response.read())["model"]


def test_a_query_parameter_is_accepted(guarded: Running) -> None:
    """So a link can be handed over without asking anyone to craft a header."""
    assert guarded.get(f"/api/overview?token={TOKEN}").status == 200


def test_the_page_hands_back_a_cookie_so_the_token_need_not_trail_every_url(
    guarded: Running,
) -> None:
    response = guarded.get(f"/?token={TOKEN}")
    assert response.status == 200

    cookies = response.getheader("Set-Cookie") or ""
    assert "concordance_token" in cookies

    # And that cookie alone then satisfies a later request.
    follow_up = guarded.get(
        "/api/overview", {"Cookie": f"concordance_token={TOKEN}"}
    )
    assert follow_up.status == 200


# -- the whole surface, not just the front door -------------------------------

def test_every_read_route_is_guarded(guarded: Running) -> None:
    """An auth check that covers the page but not the API looks protected and
    is not. Derived from the route table so a new endpoint cannot be added
    outside the guard without this failing."""
    for route in sorted(api.ROUTES):
        assert guarded.get(route).status == 401, f"{route} was reachable unauthenticated"


def test_the_page_itself_is_guarded(guarded: Running) -> None:
    assert guarded.get("/").status == 401


def test_the_chat_is_guarded(guarded: Running) -> None:
    """The endpoint that actually spends money is the one that most needs it."""
    response = guarded.post("/api/ask", {"question": "hello"})
    assert response.status == 401


def test_an_unknown_route_is_refused_before_it_is_resolved(guarded: Running) -> None:
    """401 rather than 404: an unauthenticated caller should not be able to map
    which routes exist by reading the difference between the two."""
    assert guarded.get("/api/does-not-exist").status == 401


# -- the comparison itself -----------------------------------------------------

def test_the_token_is_compared_in_constant_time() -> None:
    """Pinned so a later refactor cannot quietly reintroduce `==`.

    A plain string comparison returns as soon as two characters differ, and the
    time it takes leaks how long a matching prefix was.
    """
    import inspect

    from concordance.web import server

    source = inspect.getsource(server.make_handler)
    assert "compare_digest" in source

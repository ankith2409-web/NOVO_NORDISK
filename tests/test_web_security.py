"""The web layer's security properties, asserted against a real server.

Every check here corresponds to something a public deployment made reachable
that a laptop-only tool did not. `concordance serve` binds 0.0.0.0 in the
container, `--token` is optional, and the default language model bills per
token -- so "anyone who finds the URL" stopped being a hypothetical.

These are deliberately behavioural rather than structural: they read response
headers and status codes off a running server, because a header that is present
in the source and absent from the response protects nothing, and only an actual
request can tell the difference.
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
from concordance.llm.base import Completion
from concordance.llm.fake import FakeProvider
from concordance.web import api
from concordance.web.server import RateLimiter, make_handler

MODEL = Path("data/models/QualityControl.SemanticModel")


@pytest.fixture
def graph() -> SemanticGraph:
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return SemanticGraph(TmdlAdapter().extract(str(MODEL)))


class _Server:
    def __init__(self, graph, **kwargs) -> None:
        handler, _ = make_handler(
            graph, FakeProvider(script=[Completion(text='ok') for _ in range(40)]), api.ApiContext(graph=graph), **kwargs
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def request(self, method: str, path: str, headers: dict | None = None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        return conn.getresponse()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(graph):
    running = _Server(graph)
    yield running
    running.close()


# -- headers that must be on every response ----------------------------------

@pytest.mark.parametrize("path", ["/", "/api/overview", "/api/document?kind=business"])
def test_security_headers_are_on_every_response(server, path: str) -> None:
    """Not just the HTML page. The document download is Markdown assembled from
    model content, and a browser that MIME-sniffed it as HTML would run whatever
    a crafted measure name contained -- so `nosniff` has to reach that route too.
    """
    response = server.request("GET", path)
    response.read()

    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert response.getheader("X-Frame-Options") == "DENY"
    assert "frame-ancestors 'none'" in (response.getheader("Content-Security-Policy") or "")


def test_the_interface_cannot_be_framed(server) -> None:
    """Clickjacking is the specific risk, and it is not abstract here: this
    server records review decisions through a POST. A page on another origin
    that could frame it could get a signed-in reviewer to click "accepted" on a
    statement they never saw, and a decision log whose entries may not reflect a
    real click is worth nothing at all.
    """
    response = server.request("GET", "/")
    response.read()

    policy = response.getheader("Content-Security-Policy") or ""
    assert response.getheader("X-Frame-Options") == "DENY"
    assert "frame-ancestors 'none'" in policy


def test_the_referrer_never_carries_a_url_to_another_origin(server) -> None:
    """The Auth0 SDK's CDN is the only outbound request the page makes, and it
    has no business learning which model is open."""
    response = server.request("GET", "/")
    response.read()
    assert response.getheader("Referrer-Policy") == "same-origin"


# -- cookies ------------------------------------------------------------------

def _cookies(response) -> str:
    return "; ".join(response.msg.get_all("Set-Cookie") or [])


def test_cookies_are_httponly_and_samesite(server) -> None:
    """HttpOnly keeps a credential out of reach of any script on the page;
    SameSite=Lax is what actually stops a cross-site POST carrying it, which is
    this server's CSRF defence for /api/decide."""
    response = server.request("GET", "/")
    response.read()
    issued = _cookies(response)

    assert "HttpOnly" in issued
    assert "SameSite=Lax" in issued


def test_a_cookie_is_marked_secure_when_the_request_arrived_over_https(server) -> None:
    """Render terminates TLS and forwards X-Forwarded-Proto, which is the only
    way this process can know the browser is on HTTPS."""
    response = server.request("GET", "/", headers={"X-Forwarded-Proto": "https"})
    response.read()
    assert "Secure" in _cookies(response)


def test_a_cookie_is_not_marked_secure_on_plain_http(server) -> None:
    """The other half, and the reason this is conditional rather than always on.
    A Secure cookie is simply not stored over plain HTTP, so setting it
    unconditionally would make sign-in on a laptop silently do nothing --
    a failure that looks like a bug, not like a security setting.
    """
    response = server.request("GET", "/")
    response.read()
    assert "Secure" not in _cookies(response)


def test_a_forged_forwarded_proto_grants_no_access(server) -> None:
    """X-Forwarded-Proto is client-controllable when nothing sits in front of
    this server, so it must not be able to buy anything.

    Asserted behaviourally rather than by reading the source: claiming HTTPS
    changes only the Secure flag on the cookie, and leaves every status code
    exactly where it was. The worst a lie achieves is a cookie the browser then
    declines to send back over plain HTTP -- it degrades that client's own
    session and grants nothing.
    """
    honest = server.request("GET", "/api/overview")
    honest.read()
    lying = server.request("GET", "/api/overview", headers={"X-Forwarded-Proto": "https"})
    lying.read()

    assert honest.status == lying.status


# -- rate limiting ------------------------------------------------------------

def test_the_ask_endpoint_refuses_a_flood(server) -> None:
    """The endpoint that costs money. Without this, anyone with the URL can
    drain the operator's API balance in a loop -- and the container deployment
    binds 0.0.0.0 with --token optional, so "anyone with the URL" is real.
    """
    body = json.dumps({"question": "hi"})
    headers = {"Content-Type": "application/json"}

    statuses = []
    for _ in range(25):
        response = server.request("POST", "/api/ask", headers=headers, body=body)
        response.read()
        statuses.append(response.status)

    assert 429 in statuses, "a flood must eventually be refused"
    assert statuses[0] != 429, "the first question must not be refused"


def test_a_refusal_says_when_to_come_back(server) -> None:
    body = json.dumps({"question": "hi"})
    headers = {"Content-Type": "application/json"}

    last = None
    payload = b""
    for _ in range(25):
        last = server.request("POST", "/api/ask", headers=headers, body=body)
        payload = last.read()
        if last.status == 429:
            break

    assert last is not None and last.status == 429
    assert int(last.getheader("Retry-After")) > 0
    assert "Try again in" in payload.decode()


def test_read_only_routes_are_never_rate_limited(server) -> None:
    """Only the paid endpoint is limited. Reading the model costs nothing, and
    limiting it would break the interface, which issues several requests per
    view change."""
    statuses = set()
    for _ in range(40):
        response = server.request("GET", "/api/overview")
        response.read()
        statuses.add(response.status)
    assert statuses == {200}


# -- the limiter itself -------------------------------------------------------

def test_the_window_resets() -> None:
    limiter = RateLimiter(limit=2, window=60)
    assert limiter.check("a", now=0.0)[0] is True
    assert limiter.check("a", now=0.0)[0] is True
    assert limiter.check("a", now=0.0)[0] is False
    assert limiter.check("a", now=61.0)[0] is True, "a new window starts clean"


def test_clients_are_counted_separately() -> None:
    """Two reviewers behind one office NAT must not exhaust each other."""
    limiter = RateLimiter(limit=1, window=60)
    assert limiter.check("anna", now=0.0)[0] is True
    assert limiter.check("anna", now=0.0)[0] is False
    assert limiter.check("bo", now=0.0)[0] is True


def test_a_refusal_reports_a_usable_wait() -> None:
    limiter = RateLimiter(limit=1, window=60)
    limiter.check("a", now=0.0)
    allowed, retry_after = limiter.check("a", now=10.0)
    assert allowed is False
    assert 0 < retry_after <= 60


def test_the_limiter_cannot_grow_without_bound() -> None:
    """A scanner rotating source addresses would otherwise be a memory leak
    with a rate limiter's name on it."""
    limiter = RateLimiter(limit=5, window=60)
    for index in range(6000):
        limiter.check(f"client-{index}", now=float(index))
    assert len(limiter._seen) <= 4100

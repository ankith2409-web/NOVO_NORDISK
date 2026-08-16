"""The page shown to someone who has not signed in yet.

A protected server used to answer the browser's document request with
``{"error": "This server requires an access token."}``. Correct for the API,
and a wall of JSON where a sign-in form belonged -- verified by loading it in
a browser, which is the only way that particular failure is visible at all.

Two things have to stay true together, and they pull in opposite directions:
a browser asking for a page must get a page, and `fetch()` asking for JSON must
still get JSON with a 401 it can act on. Both are tested here against a real
server, because content negotiation is a header behaviour and asserting it any
other way tests something else.
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
from concordance.llm.fake import FakeProvider
from concordance.review.identity import Directory
from concordance.web import api
from concordance.web.server import make_handler
from concordance.web.signin import _wants_html, sign_in_page

MODEL = Path("data/models/ClinicalTrialSafety.SemanticModel")
TOKEN = "anna-token-long-enough-to-pass"


@pytest.fixture
def graph() -> SemanticGraph:
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return SemanticGraph(TmdlAdapter().extract(str(MODEL)))


@pytest.fixture
def users(tmp_path: Path) -> Directory:
    path = tmp_path / "users.json"
    path.write_text(json.dumps({"Anna": TOKEN}))
    path.chmod(0o600)
    return Directory.load(path)


class _Server:
    def __init__(self, graph, **kwargs) -> None:
        handler, _ = make_handler(
            graph, FakeProvider(script=[]), api.ApiContext(graph=graph), **kwargs
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def get(self, path: str, accept: str = "", cookie: str = ""):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if accept:
            headers["Accept"] = accept
        if cookie:
            headers["Cookie"] = cookie
        conn.request("GET", path, headers=headers)
        return conn.getresponse()

    @staticmethod
    def cookies(response) -> str:
        """Every Set-Cookie, as a browser would send them back.

        `getheader` returns only the first, and this server sets two -- the
        session and the credential. Reading one and calling it "the cookie" is
        how a test can pass while the thing it checks is broken.
        """
        return "; ".join(
            value.split(";")[0] for value in response.msg.get_all("Set-Cookie") or []
        )

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def guarded(graph, users):
    running = _Server(graph, users=users)
    yield running
    running.close()


# -- content negotiation ------------------------------------------------------

def test_a_browser_gets_a_page_not_json(guarded) -> None:
    response = guarded.get("/", accept="text/html,application/xhtml+xml")
    body = response.read().decode()

    assert response.status == 200, "a working sign-in form is not an error state"
    assert "text/html" in response.getheader("Content-Type")
    assert "<title>Sign in" in body
    assert not body.lstrip().startswith("{")


def test_the_api_still_gets_json_it_can_act_on(guarded) -> None:
    """The other half. An HTML page arriving where `fetch()` expected an error
    body would break the interface's own 401 handling."""
    response = guarded.get("/api/overview", accept="application/json")
    assert response.status == 401
    payload = json.loads(response.read())
    assert "error" in payload and "how" in payload


def test_the_sign_in_page_is_not_cached(guarded) -> None:
    """It reflects a credential state that changes. A cached copy shown after
    signing in would look like the sign-in silently failed."""
    response = guarded.get("/", accept="text/html")
    response.read()
    assert "no-store" in (response.getheader("Cache-Control") or "")


def test_wants_html_reads_the_accept_header() -> None:
    assert _wants_html("text/html,application/xhtml+xml,*/*;q=0.8")
    assert not _wants_html("application/json")
    assert not _wants_html("")


# -- what the page offers -----------------------------------------------------

def test_a_token_server_offers_a_token_field_and_says_what_it_is() -> None:
    page = sign_in_page(model="Demo", auth0=None, accepts_token=True).decode()
    assert 'name="token"' in page
    assert 'type="password"' in page, "a credential must not be shadow-surfed"
    # Honest about the mechanism rather than imitating a login it is not.
    assert "bearer token, not an account" in page
    assert "auth0" not in page.lower(), "no Auth0 anything when none is configured"


def test_an_auth0_server_offers_sign_in_and_signup() -> None:
    from concordance.review.auth0 import Auth0Verifier

    verifier = Auth0Verifier(
        domain="acme.eu.auth0.com", audience="https://api", client_id="spa-abc"
    )
    page = sign_in_page(model="Demo", auth0=verifier, accepts_token=True).decode()

    assert 'id="login"' in page
    assert 'id="signup"' in page
    # Signup is a Universal Login hint, not a screen this project builds.
    assert 'screen_hint: "signup"' in page
    assert "spa-abc" in page
    # Google needs no code at all -- it is a tenant setting -- so the page says
    # so rather than showing a button this server cannot make work.
    assert "Google" in page
    # And the offline way in is still there.
    assert "personal token instead" in page


def test_the_page_never_carries_a_client_secret() -> None:
    """A browser app cannot hold one. Asserted so nobody later "fixes" the
    config by adding it."""
    from concordance.review.auth0 import Auth0Verifier

    page = sign_in_page(
        model="Demo",
        auth0=Auth0Verifier(domain="acme.eu.auth0.com", audience="a", client_id="b"),
        accepts_token=True,
    ).decode()
    assert "secret" not in page.lower()


def test_the_model_name_is_escaped() -> None:
    page = sign_in_page(model="<script>alert(1)</script>", auth0=None,
                        accepts_token=True).decode()
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


# -- the loop this caused -----------------------------------------------------

def test_an_accepted_credential_is_remembered(guarded) -> None:
    """The bug behind an infinite reload.

    The cookie used to be set only when a *shared* token was configured, and it
    stored the server's own token rather than the one presented -- so a personal
    token from the sign-in form authorised exactly one request and was then
    forgotten. Every `fetch()` after it was unauthenticated, and once the
    interface started reloading on a 401 that became a loop.
    """
    response = guarded.get(f"/?token={TOKEN}", accept="text/html")
    response.read()
    assert response.status == 200

    raw = "".join(response.msg.get_all("Set-Cookie") or [])
    assert "concordance_token=" in raw, "the credential was not remembered"
    assert TOKEN in raw
    assert "HttpOnly" in raw, "a credential readable by script is a credential leaked"


def test_the_remembered_cookie_actually_authorises(guarded) -> None:
    first = guarded.get(f"/?token={TOKEN}", accept="text/html")
    first.read()
    jar = guarded.cookies(first)

    # The request the interface makes on load, carrying only the cookie.
    again = guarded.get("/api/overview", accept="application/json", cookie=jar)
    assert again.status == 200
    again.read()


def test_an_unauthenticated_request_is_never_given_a_credential_cookie(guarded) -> None:
    response = guarded.get("/", accept="text/html")
    response.read()
    raw = "".join(response.msg.get_all("Set-Cookie") or [])
    assert "concordance_token=" not in raw

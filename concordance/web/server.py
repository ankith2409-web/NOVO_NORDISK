"""Local web server for the model chatbot.

Deliberately dependency-free: the Gemini provider already avoids an SDK in
favour of one `urllib` POST, and the web layer follows the same principle --
stdlib's ``http.server`` and ``json`` are enough for one page, one endpoint,
and per-browser session state. Nothing here needs a framework.

Session isolation matters because a real demo is rarely one person: if two
browser tabs shared a single ``ModelChat``, their questions and answers would
interleave into one conversation. Each session gets its own chat instance, and
the split (``SessionStore`` versus the HTTP handler) exists so isolation can be
tested directly, without going through sockets.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from collections import OrderedDict
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from concordance.agent.chat import ModelChat
from concordance.graph.csg import SemanticGraph
from concordance.llm.base import LlmError, LlmProvider
from concordance.web import api

_STATIC_DIR = Path(__file__).parent / "static"
_SESSION_COOKIE = "concordance_session"
#: Set once a valid token arrives, so a link carrying ?token= keeps working as
#: the interface navigates without repeating the token on every request.
_TOKEN_COOKIE = "concordance_token"
#: Refuses a request body larger than this outright, before it is read.
_MAX_BODY_BYTES = 10_000

#: A front-end dev server runs on its own port, so the browser treats calls to
#: this one as cross-origin. Only loopback origins are allowed: the API serves a
#: local model and, through the chat, spends real API quota, so echoing back
#: whatever ``Origin`` arrives would let any site the user happens to be
#: visiting read their model and run up their bill.
_LOOPBACK_ORIGIN = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$")


def allowed_origin(origin: str | None) -> str | None:
    """The value to echo in ``Access-Control-Allow-Origin``, or None to refuse.

    The origin is echoed rather than answered with ``*`` because the session
    cookie makes these credentialed requests, and the wildcard is invalid once
    credentials are in play.
    """
    if origin and _LOOPBACK_ORIGIN.match(origin):
        return origin
    return None


#: Plenty for any demo audience, while bounding how much a long-running server
#: can accumulate. Every cookieless request mints a session, so without a cap a
#: crawler -- or simply a page left open for a day -- grows this without limit,
#: and each entry holds a full conversation history.
MAX_SESSIONS = 200


class SessionStore:
    """One ``ModelChat`` per browser session, with a bounded number of them.

    Kept separate from the HTTP handler so isolation is a plain unit test:
    two ids must resolve to two chats with independent history, with no server
    socket involved.

    Eviction is least-recently-used. An evicted visitor is not broken, only
    forgotten -- their next request mints a fresh session and starts a new
    conversation, which is the right failure for a chat with no durable state.

    When several models are served, one browser session holds one conversation
    *per model*. That is not an implementation convenience: a chat carries the
    tool results it has already seen, and replaying one model's tables and DAX
    into a question about another is precisely how a grounded answer stops
    being grounded. Switching models starts a clean conversation, and switching
    back returns to the one it left.
    """

    def __init__(
        self,
        factory: Callable[[str], ModelChat],
        max_sessions: int = MAX_SESSIONS,
    ) -> None:
        self._factory = factory
        self._max_sessions = max_sessions
        self._sessions: OrderedDict[tuple[str, str], ModelChat] = OrderedDict()
        self._lock = threading.Lock()

    def get(
        self, session_id: str | None, model: str = ""
    ) -> tuple[str, ModelChat]:
        """Return the chat for ``session_id`` and ``model``, creating if unknown.

        The returned id is the browser's session id -- the same one across every
        model, since it is the cookie. The model only splits the conversation
        held behind it.
        """
        with self._lock:
            if session_id and (session_id, model) in self._sessions:
                self._sessions.move_to_end((session_id, model))
                return session_id, self._sessions[(session_id, model)]

            # An id this server already issued is kept even when this model is
            # new to it, so switching models does not orphan the conversations
            # already open under it. An id we have never issued is replaced
            # rather than adopted -- ids stay server-minted, so a caller cannot
            # choose one and have it honoured.
            known = any(held == session_id for held, _ in self._sessions)
            new_id = session_id if (session_id and known) else secrets.token_urlsafe(16)
            chat = self._factory(model)
            self._sessions[(new_id, model)] = chat
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)
            return new_id, chat

    def __len__(self) -> int:
        return len(self._sessions)


def make_handler(
    graph: SemanticGraph,
    provider: LlmProvider,
    context: api.ApiContext | api.ModelRegistry | None = None,
    access_token: str = "",
) -> tuple[type[BaseHTTPRequestHandler], SessionStore]:
    """Build the request handler class for one model and provider.

    Returns the store alongside the handler so tests can inspect session state
    without an HTTP round trip.

    ``context`` carries whatever extra sources the read-only API is permitted to
    reach. Omitted, the API still serves everything derivable from the model
    itself and reports the rest as unconfigured.

    ``access_token``, when set, is required on every request. It is off by
    default because the server binds to loopback and the overwhelmingly common
    use is one person on their own machine, where a login would be friction
    protecting nothing. It exists because the moment that changes -- a shared
    machine, a bound interface, a tunnel opened so a colleague can look -- the
    server is handing out a company's DAX logic and letting anyone who finds
    the port spend its API quota. A shared token is not real identity, and does
    not pretend to be; it is the smallest honest control for that situation.
    """
    registry = context if context is not None else api.ApiContext(graph=graph)
    if not isinstance(registry, api.ModelRegistry):
        registry = api.ModelRegistry.of(registry)

    def _chat_for(model: str) -> ModelChat:
        chosen = registry.contexts.get(model or registry.default)
        return ModelChat(chosen.graph if chosen else graph, provider)

    sessions = SessionStore(_chat_for)
    page_template = (_STATIC_DIR / "chat.html").read_text(encoding="utf-8")
    page = page_template.replace("{{MODEL_NAME}}", graph.model.name)
    page_bytes = page.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "ConcordanceChat/0.1"

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # quiet by default; failures still surface in HTTP responses

        def do_GET(self) -> None:  # noqa: N802 -- fixed by BaseHTTPRequestHandler
            parsed = urlparse(self.path)
            if not self._authorised(parse_qs(parsed.query)):
                return
            if parsed.path == "/":
                self._serve_page()
            elif parsed.path in api.ALL_ROUTES:
                self._serve_api(parsed.path, parse_qs(parsed.query))
            else:
                self._not_found()

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if not self._authorised(parse_qs(parsed.query)):
                return
            if parsed.path == "/api/ask":
                self._handle_ask()
            else:
                self._not_found()

        def do_OPTIONS(self) -> None:  # noqa: N802
            """Answer the browser's CORS preflight."""
            self.send_response(HTTPStatus.NO_CONTENT)
            self._cors_headers()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        # -- routes ----------------------------------------------------

        def _serve_page(self) -> None:
            session_id, _ = sessions.get(self._session_cookie(), registry.default)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page_bytes)))
            self._set_session_cookie(session_id)
            if access_token:
                # Arriving with a valid ?token= is what got us here; remembering
                # it means the rest of the interface works without the token
                # trailing every URL the user then sees.
                self.send_header(
                    "Set-Cookie",
                    f"{_TOKEN_COOKIE}={access_token}; Path=/; HttpOnly; SameSite=Lax",
                )
            self.end_headers()
            self.wfile.write(page_bytes)

        def _serve_api(self, path: str, params: dict[str, list[str]]) -> None:
            """Read-only endpoints: no session, since the graph never changes."""
            status, payload = api.handle(registry, path, params)
            self._json(status, payload)

        def _handle_ask(self) -> None:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length == 0 or length > _MAX_BODY_BYTES:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "request body must be non-empty and under 10KB"},
                )
                return

            try:
                payload = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "malformed JSON body"})
                return

            question = str(payload.get("question", "")).strip()
            if not question:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "question must not be empty"})
                return

            model = str(payload.get("model", "") or "").strip()
            if model and model not in registry.contexts:
                # Answering out of the default model instead would produce a
                # confident answer about the wrong model, which is worse than
                # a refusal.
                self._json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "error": "that model is not loaded on this server",
                        "loaded": sorted(registry.contexts),
                    },
                )
                return

            # Normalised so an explicit default-model name and an omitted one
            # share one conversation rather than quietly forking into two.
            session_id, chat = sessions.get(
                self._session_cookie(), model or registry.default
            )
            try:
                exchange = chat.ask(question)
            except LlmError as error:
                # 502: the request into this server was fine; the upstream
                # provider is what failed.
                self._json(HTTPStatus.BAD_GATEWAY, {"error": str(error)})
                return

            self._json(
                HTTPStatus.OK,
                {
                    "answer": exchange.answer,
                    "grounded": exchange.grounded,
                    "tool_calls": [
                        {"name": name, "arguments": arguments}
                        for name, arguments in exchange.tool_calls
                    ],
                    "rejected_calls": exchange.rejected_calls,
                },
                session_id=session_id,
            )

        # -- helpers -----------------------------------------------------

        def _cookie(self, name: str) -> str | None:
            raw = self.headers.get("Cookie")
            if not raw:
                return None
            jar: SimpleCookie = SimpleCookie()
            jar.load(raw)
            morsel = jar.get(name)
            return morsel.value if morsel else None

        def _session_cookie(self) -> str | None:
            return self._cookie(_SESSION_COOKIE)

        def _set_session_cookie(self, session_id: str) -> None:
            self.send_header(
                "Set-Cookie", f"{_SESSION_COOKIE}={session_id}; Path=/; HttpOnly; SameSite=Lax"
            )

        def _authorised(self, params: dict[str, list[str]]) -> bool:
            """Check the shared token, when one is configured.

            Accepted from an ``Authorization: Bearer`` header, a cookie set on
            first arrival, or a ``?token=`` query parameter -- the last so a
            link can be handed to someone without asking them to craft a
            header. Compared with ``compare_digest`` rather than ``==`` so the
            comparison does not leak the token's length or its matching prefix
            through how long it takes to fail.
            """
            if not access_token:
                return True

            supplied = ""
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                supplied = header[len("Bearer ") :].strip()
            if not supplied:
                supplied = (params.get("token") or [""])[0]
            if not supplied:
                supplied = self._cookie(_TOKEN_COOKIE) or ""

            if supplied and secrets.compare_digest(supplied, access_token):
                return True

            self._json(
                HTTPStatus.UNAUTHORIZED,
                {
                    "error": "This server requires an access token.",
                    "how": "Open the link printed by `concordance serve`, which "
                    "carries the token, or send it as an Authorization: Bearer header.",
                },
            )
            return False

        def _cors_headers(self) -> None:
            origin = allowed_origin(self.headers.get("Origin"))
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
            # Sent even when the origin is refused: the response varies by
            # Origin either way, and a cache that misses this would serve one
            # origin's answer to another.
            self.send_header("Vary", "Origin")

        def _json(
            self, status: HTTPStatus, payload: dict, *, session_id: str | None = None
        ) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors_headers()
            if session_id:
                self._set_session_cookie(session_id)
            self.end_headers()
            self.wfile.write(body)

        def _not_found(self) -> None:
            self._json(
                HTTPStatus.NOT_FOUND,
                {"error": f"no such route: {self.path}", "routes": list(api.ALL_ROUTES)},
            )

    return Handler, sessions


def _capabilities_of(context: api.ApiContext) -> list[str]:
    """The optional features one loaded model can actually answer for.

    Both are opt-in at startup: drift needs a second model to compare against,
    reconcile needs a warehouse connection. Naming them in the banner is how
    someone finds out a flag they passed did not take effect.
    """
    enabled = []
    if context.compare_to is not None:
        enabled.append("drift")
    if context.warehouse is not None:
        enabled.append("reconcile")
    return enabled


def serve(
    graph: SemanticGraph,
    provider: LlmProvider,
    host: str = "127.0.0.1",
    port: int = 8000,
    context: api.ApiContext | api.ModelRegistry | None = None,
    access_token: str = "",
) -> None:
    """Run the chat server until interrupted."""
    handler, _ = make_handler(graph, provider, context, access_token=access_token)
    httpd = ThreadingHTTPServer((host, port), handler)
    base = f"http://{host}:{httpd.server_port}/"
    # The printed link carries the token, so the person who started the server
    # can simply click it. Anyone else has to be given it deliberately, which is
    # the entire point.
    url = f"{base}?token={access_token}" if access_token else base
    print(f"Concordance chat for {graph.model.name!r} — {url}")
    if access_token:
        print("  access token required — share the link above to grant access")
    elif host not in ("127.0.0.1", "localhost", "::1"):
        # Bound beyond loopback with nothing in front of it. Said plainly rather
        # than left for someone to discover.
        print(
            f"  WARNING: listening on {host} with no access token. Anyone who can "
            f"reach this port can read this model's DAX and spend its API quota. "
            f"Pass --token to require one.",
        )
    if context is not None:
        # Normalised the same way the handler does, so the banner cannot drift
        # out of step with what is actually served -- reading `.compare_to` off
        # a registry is exactly the mistake this avoids.
        registry = (
            context
            if isinstance(context, api.ModelRegistry)
            else api.ModelRegistry.of(context)
        )
        if len(registry.contexts) > 1:
            print(f"  {len(registry.contexts)} models loaded:")
            for name in sorted(registry.contexts):
                marker = "  (default)" if name == registry.default else ""
                enabled = _capabilities_of(registry.contexts[name])
                suffix = f" — {', '.join(enabled)}" if enabled else ""
                print(f"    {name}{marker}{suffix}")
        else:
            enabled = _capabilities_of(registry.contexts[registry.default])
            if enabled:
                print(f"  also serving: {', '.join(enabled)}")
    # `base`, not `url`: the latter may carry ?token=, which would splice the
    # query string into the middle of the path and print a nonsense address.
    print(f"  api: {len(api.ALL_ROUTES)} read-only endpoints under {base}api/")
    print("Press Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()

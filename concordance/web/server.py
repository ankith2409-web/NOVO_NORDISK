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
import secrets
import threading
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from concordance.agent.chat import ModelChat
from concordance.graph.csg import SemanticGraph
from concordance.llm.base import LlmError, LlmProvider

_STATIC_DIR = Path(__file__).parent / "static"
_SESSION_COOKIE = "concordance_session"
#: Refuses a request body larger than this outright, before it is read.
_MAX_BODY_BYTES = 10_000


class SessionStore:
    """One ``ModelChat`` per browser session.

    Kept separate from the HTTP handler so isolation is a plain unit test:
    two ids must resolve to two chats with independent history, with no server
    socket involved.
    """

    def __init__(self, factory: Callable[[], ModelChat]) -> None:
        self._factory = factory
        self._sessions: dict[str, ModelChat] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str | None) -> tuple[str, ModelChat]:
        """Return the chat for ``session_id``, creating one if it is unknown."""
        with self._lock:
            if session_id and session_id in self._sessions:
                return session_id, self._sessions[session_id]
            new_id = secrets.token_urlsafe(16)
            chat = self._factory()
            self._sessions[new_id] = chat
            return new_id, chat

    def __len__(self) -> int:
        return len(self._sessions)


def make_handler(
    graph: SemanticGraph, provider: LlmProvider
) -> tuple[type[BaseHTTPRequestHandler], SessionStore]:
    """Build the request handler class for one model and provider.

    Returns the store alongside the handler so tests can inspect session state
    without an HTTP round trip.
    """
    sessions = SessionStore(lambda: ModelChat(graph, provider))
    page_template = (_STATIC_DIR / "chat.html").read_text(encoding="utf-8")
    page = page_template.replace("{{MODEL_NAME}}", graph.model.name)
    page_bytes = page.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "ConcordanceChat/0.1"

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # quiet by default; failures still surface in HTTP responses

        def do_GET(self) -> None:  # noqa: N802 -- fixed by BaseHTTPRequestHandler
            if self.path == "/":
                self._serve_page()
            elif self.path == "/api/overview":
                self._serve_overview()
            else:
                self._not_found()

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/api/ask":
                self._handle_ask()
            else:
                self._not_found()

        # -- routes ----------------------------------------------------

        def _serve_page(self) -> None:
            session_id, _ = sessions.get(self._session_cookie())
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page_bytes)))
            self._set_session_cookie(session_id)
            self.end_headers()
            self.wfile.write(page_bytes)

        def _serve_overview(self) -> None:
            _, chat = sessions.get(self._session_cookie())
            self._json(HTTPStatus.OK, chat.tools.overview())

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

            session_id, chat = sessions.get(self._session_cookie())
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

        def _session_cookie(self) -> str | None:
            raw = self.headers.get("Cookie")
            if not raw:
                return None
            jar: SimpleCookie = SimpleCookie()
            jar.load(raw)
            morsel = jar.get(_SESSION_COOKIE)
            return morsel.value if morsel else None

        def _set_session_cookie(self, session_id: str) -> None:
            self.send_header(
                "Set-Cookie", f"{_SESSION_COOKIE}={session_id}; Path=/; HttpOnly; SameSite=Lax"
            )

        def _json(
            self, status: HTTPStatus, payload: dict, *, session_id: str | None = None
        ) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if session_id:
                self._set_session_cookie(session_id)
            self.end_headers()
            self.wfile.write(body)

        def _not_found(self) -> None:
            self._json(HTTPStatus.NOT_FOUND, {"error": f"no such route: {self.path}"})

    return Handler, sessions


def serve(
    graph: SemanticGraph,
    provider: LlmProvider,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run the chat server until interrupted."""
    handler, _ = make_handler(graph, provider)
    httpd = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{httpd.server_port}/"
    print(f"Concordance chat for {graph.model.name!r} — {url}")
    print("Press Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()

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
import time
from collections import OrderedDict
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from concordance.adapters.base import SourceError
from concordance.agent.chat import ModelChat
from concordance.generate import document
from concordance.graph.csg import SemanticGraph
from concordance.llm.base import LlmError, LlmProvider
from concordance.review.auth0 import Auth0Error
from concordance.web import api, upload
from concordance.web.signin import _wants_html, sign_in_page

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


#: Read-only routes this module serves that are not in ``api.ROUTES``.
#:
#: ``api.ROUTES`` maps a path to a function returning JSON, and the document
#: download answers with the document itself -- so it cannot live there without
#: making every other route's contract a lie. It still has to appear wherever
#: routes are counted or listed, though: a route the 404 handler does not
#: mention is a route nobody discovers.
_EXTRA_GET_ROUTES = ("/api/document",)


def served_routes() -> tuple[str, ...]:
    """Every read-only GET route, for the banner and the 404 body alike."""
    return tuple(sorted(api.ALL_ROUTES + _EXTRA_GET_ROUTES))


#: Characters allowed to survive into a download filename. Everything else is
#: dropped rather than escaped: a filename reaches the browser inside a quoted
#: header value, and the only way a quote or a newline in a model name can never
#: close that quote or start a second header is for it not to arrive at all.
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _drift_report(context):
    """The comparison for this model, or None if it was not given one."""
    if context.compare_to is None:
        return None
    from concordance.drift import snapshot as snap
    from concordance.drift.compare import compare

    return compare(
        snap.take(context.compare_to, label=context.compare_label or "the previous version"),
        snap.take(context.graph, label=context.graph.model.name),
        after_graph=context.graph,
    )


def _download_name(model_name: str, kind: str, fmt: str = "md") -> str:
    """A safe, descriptive filename for a generated document.

    Descriptive because someone downloading a BRD for three models wants three
    distinguishable files in their downloads folder, not ``document (2).md``.
    """
    stem = _FILENAME_SAFE.sub("-", model_name).strip("-") or "model"
    suffix = "BRD" if kind == "business" else "FRD"
    extension = "docx" if fmt == "docx" else "md"
    return f"{stem}-{suffix}.{extension}"


def allowed_origin(origin: str | None) -> str | None:
    """The value to echo in ``Access-Control-Allow-Origin``, or None to refuse.

    The origin is echoed rather than answered with ``*`` because the session
    cookie makes these credentialed requests, and the wildcard is invalid once
    credentials are in play.
    """
    if origin and _LOOPBACK_ORIGIN.match(origin):
        return origin
    return None


#: The full interface, built by `npm run build:embedded` into one inlined file.
#: Checked in, so `concordance serve` gives someone who cloned this repo the
#: whole thing without needing Node -- the alternative is a command that quietly
#: serves a lesser page than the one the project is about.
_APP_PAGE = _STATIC_DIR / "app.html"
#: The original chat-only page. Kept as the fallback for a source checkout where
#: the built app is genuinely absent, so `serve` still does something useful
#: rather than 500.
_CHAT_PAGE = _STATIC_DIR / "chat.html"


def _page_for(model_name: str) -> bytes:
    """The page served at ``/``: the built interface if present, else the chat.

    Preferring the built app is the point. For a long stretch this server had
    the React interface sitting in the repo and served the chat page instead,
    so the one command anyone would naturally run showed the least of what the
    project does -- which is a documentation problem masquerading as a routing
    default.
    """
    source = _APP_PAGE if _APP_PAGE.is_file() else _CHAT_PAGE
    return source.read_text(encoding="utf-8").replace(
        "{{MODEL_NAME}}", model_name
    ).encode("utf-8")


def serves_full_interface() -> bool:
    """Whether the built interface is available to serve, for the banner."""
    return _APP_PAGE.is_file()


#: Plenty for any demo audience, while bounding how much a long-running server
#: can accumulate. Every cookieless request mints a session, so without a cap a
#: crawler -- or simply a page left open for a day -- grows this without limit,
#: and each entry holds a full conversation history.
MAX_SESSIONS = 200


#: Requests a single client may make to the language model per window, and how
#: long that window is. Deliberately generous for a person -- a reviewer asking
#: back-to-back questions will not notice it -- and deliberately finite, because
#: this endpoint is the only one that costs money per call.
#:
#: It exists because of a specific, reachable situation rather than a
#: theoretical one: `concordance serve` binds to 0.0.0.0 in the container
#: deployment, `--token` is optional, and the default OpenRouter model bills per
#: token. That combination means anyone who finds the URL can spend the
#: operator's balance in a loop, and nothing else in this server would stop them.
_ASK_LIMIT = 20
_ASK_WINDOW_SECONDS = 60.0

#: The same idea for uploads, over a longer window. An upload costs no API
#: credit, but it does cost a file parse and a temporary directory, and the
#: honest number for a person is low: uploading six models in five minutes is
#: not something anyone does by hand.
_UPLOAD_LIMIT = 6
_UPLOAD_WINDOW_SECONDS = 300.0


class RateLimiter:
    """A fixed-window limiter, keyed by client.

    A token bucket would smooth bursts more gracefully; a fixed window is used
    instead because it needs one timestamp and one counter per client and can be
    read at a glance. The failure mode of a fixed window -- up to double the
    limit across a boundary -- costs a handful of extra questions, which is not
    the risk this is defending against.

    Kept out of the HTTP handler for the same reason ``SessionStore`` is: the
    interesting behaviour is testable without opening a socket.
    """

    def __init__(self, limit: int = _ASK_LIMIT, window: float = _ASK_WINDOW_SECONDS) -> None:
        self.limit = limit
        self.window = window
        self._seen: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def check(self, client: str, now: float | None = None) -> tuple[bool, int]:
        """Record one request and say whether it is allowed, plus seconds to wait.

        Returns ``(allowed, retry_after)``. ``retry_after`` is only meaningful
        when refused, and is what the 429's ``Retry-After`` header carries so a
        caller is told when to come back rather than left to guess.
        """
        moment = time.monotonic() if now is None else now
        with self._lock:
            started, count = self._seen.get(client, (moment, 0))

            if moment - started >= self.window:
                started, count = moment, 0

            # Bounded so a stream of distinct clients -- a scanner rotating
            # source addresses -- cannot grow this dictionary without limit.
            # Evicting the oldest window is right: those entries are the ones
            # closest to expiring anyway.
            if len(self._seen) > 4096 and client not in self._seen:
                oldest = min(self._seen, key=lambda key: self._seen[key][0])
                del self._seen[oldest]

            count += 1
            self._seen[client] = (started, count)

            if count > self.limit:
                return False, max(1, int(self.window - (moment - started)) + 1)
            return True, 0


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


#: How many uploaded models one browser session may hold at once, and how many
#: this server keeps across every session.
#:
#: The per-session cap is small because uploading a fourth model is almost
#: always replacing the third, not collecting them; the global one bounds a
#: public deployment, where every visitor gets an allowance and nothing else
#: would stop a hundred of them from being held at once. A graph is a few
#: megabytes, so the ceiling is memory, not correctness.
MAX_UPLOADS_PER_SESSION = 3
MAX_UPLOADS = 24


class UploadStore:
    """Models visitors uploaded, held in memory and owned by one session each.

    The invariant this exists to keep: an uploaded model is visible to the
    browser session that uploaded it and to nobody else. Configured models are
    shared because an operator chose to share them; an upload is somebody's
    proprietary Power BI file arriving at a URL, and a demo server that quietly
    showed it to the next visitor would be the single worst bug this project
    could have.

    Names are unique across the whole server even though visibility is not.
    That is what lets the chat find a graph from a model name alone, without
    threading a session id through ``SessionStore``'s factory -- and it is safe
    because a name is only ever *resolvable* through ``for_session``, which
    hands back one session's models and no other's. Uniqueness makes the lookup
    unambiguous; ownership is what makes it private.

    Bounded twice, per session and in total, and least-recently-used in both
    directions. An evicted model is not an error: its owner is told which one
    went, and can upload it again.
    """

    def __init__(
        self,
        reserved: set[str],
        max_per_session: int = MAX_UPLOADS_PER_SESSION,
        max_total: int = MAX_UPLOADS,
    ) -> None:
        #: Names this server was started with. An upload never shadows one --
        #: it would make `?model=QualityControl` mean different models for
        #: different people, and silently hide a configured model from whoever
        #: uploaded something similarly named.
        self._reserved = set(reserved)
        self._max_per_session = max_per_session
        self._max_total = max_total
        self._held: OrderedDict[str, tuple[str, api.ApiContext]] = OrderedDict()
        #: Every name this store has *ever* issued, including ones since evicted
        #: or forgotten.
        #:
        #: Disambiguating against what is currently held would let a name come
        #: back. Upload five models and the fifth is issued the first's name,
        #: because the first fell off the per-session cap and freed it -- so a
        #: link, a second tab or a downloaded FRD that names "QualityControl
        #: (2)" now resolves to a different model, under the name its reader
        #: associates with the old one. That is one model's figures under
        #: another's name, which is the single failure this project exists to
        #: prevent, so a name is spent once and never reissued.
        self._ever: set[str] = set()
        self._lock = threading.Lock()

    def add(
        self, session_id: str, context: api.ApiContext, name: str
    ) -> tuple[str, str]:
        """Hold ``context`` for ``session_id``. Returns its name and any evicted.

        The returned name may not be the one asked for: a model called the same
        as one already here is given a suffix rather than allowed to replace it,
        so uploading a second version of something never makes the first
        unreachable.

        **Takes ownership of ``context`` and the graph inside it**, which it
        renames to match the name it settles on. The caller must not keep using
        the graph afterwards. That is a fair contract here -- the only caller
        hands over a graph it parsed moments earlier from an upload, which
        nothing else has a reference to -- and it is what lets the rename happen
        under the same lock that chose the name, with no window where the model
        is reachable under a name it does not carry.
        """
        with self._lock:
            chosen = self._free_name(name)
            # The model is renamed to match the key it is filed under, so the
            # switcher and the page it opens agree. Without this a disambiguated
            # upload appears as "QualityControl (2)" in the header and
            # "QualityControl" on every page inside it -- two names for one
            # thing, next to the original it was disambiguated from.
            #
            # Safe to do after the graph is built: node ids are keyed on table
            # and object name, and requirement ids deliberately exclude the
            # model's name so a renamed file does not invalidate every decision
            # recorded against it.
            context.graph.model.name = chosen
            self._ever.add(chosen)
            self._held[chosen] = (session_id, context)

            evicted = ""
            mine = [held for held, (owner, _) in self._held.items() if owner == session_id]
            while len(mine) > self._max_per_session:
                evicted = mine.pop(0)
                del self._held[evicted]

            while len(self._held) > self._max_total:
                # Oldest overall, which by construction belongs to whoever has
                # been away longest.
                self._held.popitem(last=False)
            return chosen, evicted

    def _free_name(self, name: str) -> str:
        base = name or "uploaded model"
        if self._available(base):
            return base
        for attempt in range(2, 1000):
            candidate = f"{base} ({attempt})"
            if self._available(candidate):
                return candidate
        return f"{base} ({secrets.token_hex(3)})"

    def _available(self, candidate: str) -> bool:
        return candidate not in self._reserved and candidate not in self._ever

    def for_session(self, session_id: str | None) -> dict[str, api.ApiContext]:
        """Everything ``session_id`` uploaded, for layering onto the registry."""
        if not session_id:
            return {}
        with self._lock:
            found = {
                name: context
                for name, (owner, context) in self._held.items()
                if owner == session_id
            }
            # Touched so a session actively using its uploads is not the one
            # evicted when the server fills up.
            for name in found:
                self._held.move_to_end(name)
            return found

    def graph_of(self, name: str) -> SemanticGraph | None:
        """The graph behind an uploaded name, for the chat.

        Deliberately not access-controlled, and deliberately only called after
        the request has already resolved that name through ``for_session``.
        Both halves matter: this is the lookup, not the permission check, and
        putting the check here as well would imply the caller need not do it.
        """
        with self._lock:
            held = self._held.get(name)
            return held[1].graph if held else None

    def forget(self, session_id: str, name: str) -> bool:
        """Drop one of ``session_id``'s uploads. False if it is not theirs."""
        with self._lock:
            held = self._held.get(name)
            if not held or held[0] != session_id:
                return False
            del self._held[name]
            return True

    def __len__(self) -> int:
        return len(self._held)


def make_handler(
    graph: SemanticGraph,
    provider: LlmProvider,
    context: api.ApiContext | api.ModelRegistry | None = None,
    access_token: str = "",
    users=None,
    auth0=None,
    accepts_uploads: bool = True,
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

    ``users`` is a ``review.identity.Directory``, and it is what turns the
    review log's author from a claim into a fact. Each person presents their
    own token, the server resolves it to a name, and that name is what gets
    written -- never the one in the request body, which a caller could set to
    anybody's. Supplying it also implies access control: there is no sense in
    identifying reviewers on a server that lets an unidentified one in.

    ``accepts_uploads`` is on by default and is what lets a visitor read their
    own model without a shell. It is a flag rather than a constant because the
    two deployments want opposite answers: on a laptop, or on the demo server
    this project ships, the whole point is that somebody can drop their .pbix in
    and see it documented; on a server pointed at one audited model, an endpoint
    that parses arbitrary uploaded files is surface nobody asked for. Turning it
    off removes the route, not just the button.

    ``auth0`` is an ``Auth0Verifier`` and is the same idea reached for at a
    larger scale: it answers offboarding, password policy and "is this person
    still employed", which a JSON file cannot, and brings signup and Google
    sign-in with it. It does not replace ``users`` -- the normal deployment of
    this tool is a laptop inside a regulated network, and requiring an internet
    round-trip to read a local model would lock that person out of it.
    """
    registry = context if context is not None else api.ApiContext(graph=graph)
    if not isinstance(registry, api.ModelRegistry):
        registry = api.ModelRegistry.of(registry)

    uploads = UploadStore(reserved=set(registry.contexts))

    def _chat_for(model: str) -> ModelChat:
        chosen = registry.contexts.get(model or registry.default)
        if chosen is not None:
            return ModelChat(chosen.graph, provider)
        # An uploaded model. Reached only after the request has resolved the
        # name against the asking session's own registry, so this is a lookup
        # of something already known to belong to them.
        uploaded = uploads.graph_of(model)
        return ModelChat(uploaded if uploaded is not None else graph, provider)

    sessions = SessionStore(_chat_for)
    ask_limiter = RateLimiter()
    # Uploads are bounded but not free: each one parses a file this server did
    # not choose. The chat's limiter counts questions, so a second one counts
    # uploads rather than letting them share an allowance -- exhausting the
    # upload budget must not stop somebody asking a question.
    upload_limiter = RateLimiter(limit=_UPLOAD_LIMIT, window=_UPLOAD_WINDOW_SECONDS)
    page_bytes = _page_for(graph.model.name)
    # Rendered once: it depends only on how the server was started.
    sign_in_bytes = sign_in_page(
        model=graph.model.name,
        auth0=auth0,
        accepts_token=users is not None or bool(access_token),
    )

    class Handler(BaseHTTPRequestHandler):

        def end_headers(self) -> None:
            """Attach the headers every response should carry, then finish.

            Done by overriding rather than at each `send_response` site: there
            are a dozen of those and a header that protects only eleven of them
            protects nothing. Overriding here means a route added later gets
            them without its author having to know they exist.

            `frame-ancestors 'none'` is the one that matters most. This server
            records review decisions through a POST, so without it a page on
            another origin could frame the interface and trick a signed-in
            reviewer into clicking "accepted" on a statement they never read --
            and a decision log whose entries might not reflect a real click is
            worth nothing. `X-Frame-Options` says the same thing for browsers
            predating `frame-ancestors`.

            `nosniff` matters for the document download specifically: it serves
            Markdown built from model content, and a browser that sniffed that
            as HTML would execute whatever a crafted measure name contained.
            """
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'")
            # Keeps the model name in a URL from reaching an external host --
            # the Auth0 SDK's CDN is the only outbound request the page makes,
            # and it has no business knowing which model is open.
            self.send_header("Referrer-Policy", "same-origin")
            super().end_headers()

        def _client_key(self) -> str:
            """Who to count this request against.

            The presented credential when there is one -- that identifies a
            person, and two reviewers behind one office NAT should not exhaust
            each other's allowance. Falling back to the address otherwise.

            `X-Forwarded-For` is read only for its first entry and only because
            this runs behind a proxy that sets it; every client address would
            otherwise be the proxy's, collapsing the entire internet into a
            single bucket. It is spoofable by anyone talking to the server
            directly, which is why it is a rate-limit key and never an
            authentication input.
            """
            if self.credential:
                return f"credential:{self.credential}"
            forwarded = self.headers.get("X-Forwarded-For", "")
            if forwarded:
                return f"ip:{forwarded.split(',')[0].strip()}"
            return f"ip:{self.client_address[0]}"

        def _secure_cookie(self) -> str:
            """`; Secure` when the request arrived over HTTPS, else nothing.

            Conditional rather than always-on because both deployments are
            real: a container behind Render terminates TLS and forwards
            `X-Forwarded-Proto: https`, while `concordance serve` on a laptop is
            plain HTTP on loopback. Marking a cookie Secure unconditionally
            would stop it being stored at all in the second case, which reads as
            "sign-in silently does nothing" rather than as a security setting.
            """
            forwarded = self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip()
            return "; Secure" if forwarded.lower() == "https" else ""

        server_version = "ConcordanceChat/0.1"
        #: Set by `_authorised` from the token this request presented, for the
        #: lifetime of that request only. Empty when no user directory is
        #: configured, which is what makes an author a claim rather than a fact.
        person = ""
        #: The credential this request presented, once it has been accepted.
        #: Empty until then -- nothing unverified is ever echoed into a cookie.
        credential = ""

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # quiet by default; failures still surface in HTTP responses

        def do_GET(self) -> None:  # noqa: N802 -- fixed by BaseHTTPRequestHandler
            parsed = urlparse(self.path)
            if not self._authorised(parse_qs(parsed.query)):
                return
            if parsed.path == "/signed-out":
                # Reached after Auth0 clears its own session. Shows the sign-in
                # page rather than bouncing straight back into a login loop.
                self._serve_sign_in(clear=True)
                return
            if parsed.path == "/":
                self._serve_page()
            elif parsed.path == "/api/whoami":
                # Answered here rather than in `api`, because it is the only
                # question whose answer depends on the request rather than on
                # the model -- everything under `api` is deliberately a pure
                # function of a context and some parameters.
                self._json(
                    HTTPStatus.OK,
                    {
                        "person": self.person,
                        "identified": bool(self.person),
                        "identifies_reviewers": users is not None or auth0 is not None,
                        "auth0": auth0 is not None,
                    },
                )
            elif parsed.path == "/api/document":
                self._serve_document(parse_qs(parsed.query))
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
            elif parsed.path == "/api/session":
                self._handle_session()
            elif parsed.path == "/api/decide":
                self._handle_decide(parse_qs(parsed.query))
            elif parsed.path == "/api/upload":
                self._handle_upload(parse_qs(parsed.query))
            elif parsed.path == "/api/forget":
                self._handle_forget()
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
            if self.credential:
                # Remember whatever credential actually authorised this request,
                # so the rest of the interface works without the token trailing
                # every URL the user then sees.
                #
                # It used to store the server's *shared* token, and only when
                # one was configured -- so a personal token from the sign-in
                # form authorised exactly one request and was then forgotten,
                # leaving every subsequent fetch() unauthenticated. That was
                # invisible until the interface started reacting to a 401.
                self.send_header(
                    "Set-Cookie",
                    f"{_TOKEN_COOKIE}={self.credential}; Path=/; HttpOnly; SameSite=Lax{self._secure_cookie()}",
                )
            self.end_headers()
            self.wfile.write(page_bytes)

        def _serve_sign_in(self, clear: bool = False) -> None:
            """The page shown to someone who has not signed in yet.

            200, not 401: this *is* the page for that URL in that state, and a
            browser showing an error status for a working sign-in form invites
            the reader to think something is broken.
            """
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(sign_in_bytes)))
            self.send_header("Cache-Control", "no-store")
            if clear:
                self.send_header(
                    "Set-Cookie",
                    f"{_TOKEN_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{self._secure_cookie()}",
                )
            self.end_headers()
            self.wfile.write(sign_in_bytes)

        def _registry(self) -> api.ModelRegistry:
            """What this request may address: the server's models plus its own.

            The one place uploads enter the read path, and the reason no route
            needed changing to support them. Built per request rather than held,
            because "its own" is a property of the caller: two browsers hitting
            the same endpoint at the same moment must not be able to see each
            other's models, and a shared registry could not express that.
            """
            return registry.plus(uploads.for_session(self._session_cookie()))

        def _serve_api(self, path: str, params: dict[str, list[str]]) -> None:
            """Read-only endpoints: no session, since the graph never changes.

            The graph still never changes -- an upload adds one rather than
            mutating any -- so this remains free of session state beyond
            reading the cookie to know which models to offer.
            """
            status, payload = api.handle(self._registry(), path, params)
            self._json(status, payload)

        def _serve_document(self, params: dict[str, list[str]]) -> None:
            """The BRD or FRD as a downloadable Markdown file.

            Kept out of ``api.ROUTES`` on purpose: everything there answers with
            JSON, and this answers with the document itself. Wrapping Markdown
            in a JSON string only to have the browser unwrap it would make the
            file a thing the interface has to reassemble rather than something
            the user can save, and `Content-Disposition` is what makes it save
            rather than render.

            The rendering is `generate.document`, the same code path `concordance
            generate` uses -- a second renderer would be a second thing to keep
            true, and the whole point of this project is that the document is
            derived once and traceable.
            """
            requested = (params.get("kind") or ["business"])[0].strip().lower()
            kinds = {"business": document.Kind.BUSINESS, "functional": document.Kind.FUNCTIONAL}
            if requested not in kinds:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "kind must be 'business' or 'functional'"},
                )
                return

            fmt = (params.get("format") or ["md"])[0].strip().lower()
            if fmt not in ("md", "docx"):
                self._json(
                    HTTPStatus.BAD_REQUEST, {"error": "format must be 'md' or 'docx'"}
                )
                return

            try:
                context = self._registry().resolve(params)
            except api.ApiError as error:
                self._json(error.status, error.payload())
                return

            # An FRD asked for with a grain carries each measure's SQL. The
            # BRD never does: it states what the business needs, not how a
            # query would express it.
            grain = tuple(g for g in params.get("grain", []) if g.strip())
            dialect = (params.get("dialect") or ["duckdb"])[0].strip() or "duckdb"
            # The same comparison the Drift tab shows, carried into the
            # document. Only when this model was started with a baseline --
            # there is nothing to say otherwise, and an empty "What changed"
            # section would read as "nothing changed", which is a different
            # claim from "we were not told what to compare against".
            built = document.build(
                context.graph,
                kinds[requested],
                sql_grain=grain if grain or "sql" in params else None,
                sql_dialect=dialect,
                drift=_drift_report(context),
            )
            if fmt == "docx":
                from concordance.generate import word

                body = word.to_bytes(built)
                content_type = (
                    "application/vnd.openxmlformats-officedocument"
                    ".wordprocessingml.document"
                )
            else:
                body = document.to_markdown(built).encode("utf-8")
                content_type = "text/markdown; charset=utf-8"
            filename = _download_name(context.graph.model.name, requested, fmt)

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # `filename*` carries the real name for anything modern; the plain
            # `filename` is the ASCII fallback. Both are built from a whitelist
            # rather than escaped, so a model name can never close the quote and
            # inject another header directive.
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{filename}"',
            )
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)

        def _handle_ask(self) -> None:
            # Checked before the body is read, let alone before a provider is
            # called: the whole point is to not spend money on this request.
            allowed, retry_after = ask_limiter.check(self._client_key())
            if not allowed:
                self.send_response(HTTPStatus.TOO_MANY_REQUESTS)
                self.send_header("Content-Type", "application/json")
                self.send_header("Retry-After", str(retry_after))
                body = json.dumps(
                    {
                        "error": (
                            f"Too many questions in a short period. Try again in "
                            f"{retry_after}s. This limit exists because each "
                            f"question costs the operator of this server real "
                            f"API credit."
                        )
                    }
                ).encode("utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._cors_headers()
                self.end_headers()
                self.wfile.write(body)
                return

            payload = self._read_json()
            if payload is None:
                return

            question = str(payload.get("question", "")).strip()
            if not question:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "question must not be empty"})
                return

            model = str(payload.get("model", "") or "").strip()
            # This session's registry, not the server's: a model this person
            # uploaded is one they can ask about. It is also the access check
            # that `UploadStore.graph_of` deliberately does not perform -- a
            # name belonging to somebody else's session is not in here, so it
            # is refused below before any lookup happens.
            addressable = self._registry().contexts
            if model and model not in addressable:
                # Answering out of the default model instead would produce a
                # confident answer about the wrong model, which is worse than
                # a refusal.
                self._json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "error": "that model is not loaded on this server",
                        "loaded": sorted(addressable),
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
                    # A greeting makes no claim about the model, so the
                    # interface must not badge it "no tool used" -- that
                    # warning is for an assertion nothing verified.
                    "conversational": exchange.conversational,
                    "tool_calls": [
                        {"name": name, "arguments": arguments}
                        for name, arguments in exchange.tool_calls
                    ],
                    "rejected_calls": exchange.rejected_calls,
                },
                session_id=session_id,
            )

        def _handle_session(self) -> None:
            """Take a verified Auth0 token and put it in the session cookie.

            The token is checked here, once, before anything is stored. A
            cookie set from an unverified token would turn this endpoint into
            a way to mint a session by asking for one.

            HttpOnly, so the token is not readable by script once set -- which
            is also why the browser holds Auth0's copy in memory rather than in
            localStorage.
            """
            if auth0 is None:
                self._json(
                    HTTPStatus.NOT_IMPLEMENTED,
                    {"error": "this server was not started with an Auth0 tenant"},
                )
                return

            payload = self._read_json()
            if payload is None:
                return
            token = str(payload.get("token", "")).strip()
            try:
                identity = auth0.verify(token)
            except Auth0Error as error:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": str(error)})
                return

            body = json.dumps({"person": identity.label}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Set-Cookie",
                f"{_TOKEN_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax{self._secure_cookie()}",
            )
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)

        def _handle_decide(self, params: dict[str, list[str]]) -> None:
            """Record a review decision.

            The only endpoint on this server that writes anything. It is a POST
            for that reason, and it is behind the same access check as
            everything else -- a queue anyone passing by can sign off is not an
            audit trail.
            """
            payload = self._read_json()
            if payload is None:
                return
            try:
                context = self._registry().resolve(params)
                # The name comes from the token this request presented, never
                # from its body. A caller who could name the author could sign
                # off as a colleague, which would make the trail worthless.
                result = api.decide(context, payload, author=self.person)
            except api.ApiError as error:
                self._json(HTTPStatus(error.status), error.payload())
                return
            self._json(HTTPStatus.OK, result)

        def _handle_upload(self, params: dict[str, list[str]]) -> None:
            """Read a Power BI file out of the request body and hold it.

            The one endpoint on this server that takes a path-shaped thing from
            a caller, and it deliberately does not: the *name* arrives as
            ``?filename=``, and it is used for the extension, for the model's
            name, and for nothing else -- ``upload.safe_stem`` rebuilds it from
            a whitelist before it becomes a path. The bytes go to a temporary
            directory this server chose, are parsed, and the directory is
            removed. Nothing the caller sends decides where anything is read
            from or written to.

            The body is raw file bytes rather than a multipart form. Multipart
            would mean parsing an attacker-controlled envelope with `cgi` --
            removed in 3.13 -- or hand-writing a boundary parser, to obtain
            exactly what `fetch(url, {body: file})` already sends on its own.
            The filename is the only other field, and a query parameter carries
            it without a parser.
            """
            if not accepts_uploads:
                self._refuse_upload(
                    HTTPStatus.NOT_IMPLEMENTED,
                    "This server was started with --no-upload, so it reads "
                    "only the models it was given.",
                )
                return

            allowed, retry_after = upload_limiter.check(self._client_key())
            if not allowed:
                self._refuse_upload(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    f"Too many uploads in a short period. Try again in {retry_after}s.",
                    retry_after=retry_after,
                )
                return

            filename = (params.get("filename") or [""])[0].strip()
            if not filename:
                self._refuse_upload(
                    HTTPStatus.BAD_REQUEST,
                    "the upload needs ?filename= so its format can be read",
                )
                return

            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "that upload was empty"})
                return
            if length > upload.MAX_UPLOAD_BYTES:
                # Refused before a byte is read, so an oversized upload costs
                # this server the header and nothing else. Reading 2GB in order
                # to reject it is the denial of service, not the defence
                # against it.
                self._refuse_upload(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    f"That file is larger than the "
                    f"{upload.MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit.",
                )
                return

            # Minted before parsing, so the model is held against a session that
            # certainly exists -- a first-time visitor uploading before anything
            # else has no cookie yet, and holding their model against "" would
            # hand it to every other cookieless request.
            session_id, _ = sessions.get(self._session_cookie(), registry.default)

            try:
                uploaded = upload.read_model(self.rfile, filename, length)
            except upload.UploadRefused as refused:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(refused), "refused": True})
                return
            except SourceError as error:
                # 400 rather than 422: the file is the request. Its `render`
                # carries the hint, which is the half that tells someone what
                # to do next.
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": error.problem, "hint": error.hint, "file": error.source},
                )
                return
            except Exception as error:  # noqa: BLE001 -- last line before a 500
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": f"That file could not be read: {type(error).__name__}",
                        "hint": "Re-save it from Power BI Desktop and try again.",
                    },
                )
                return

            name, evicted = uploads.add(
                session_id,
                api.ApiContext(
                    graph=uploaded,
                    # The chat and the AI summaries work on an uploaded model
                    # exactly as they do on a configured one.
                    provider=provider,
                    # Both left unset, and both truthfully so. Drift needs a
                    # second version of this model and reconciliation needs a
                    # warehouse built for it; one uploaded file is neither, and
                    # inheriting the configured model's would compare somebody's
                    # model against a stranger's warehouse.
                    compare_to=None,
                    warehouse=None,
                    # No decision log. Requirement ids are unique only within a
                    # model, the model disappears when the session does, and on
                    # a shared server this would let a passer-by write into the
                    # operator's audit trail. The queue is readable; it is not
                    # signable.
                    decisions=None,
                    uploaded=True,
                ),
                # The adapters always name a model, but the fallback is stated
                # rather than assumed: an unnamed model would otherwise be
                # filed under "" and disambiguated into "(2)", "(3)", ...
                uploaded.model.name or upload.safe_stem(filename),
            )

            summary = uploaded.model.summary()
            self._json(
                HTTPStatus.OK,
                {
                    "name": name,
                    "source_format": uploaded.model.source_type,
                    "measures": summary["measures"],
                    "tables": summary["user_tables"],
                    "relationships": summary["relationships"],
                    # Said out loud rather than left to be noticed. Somebody who
                    # uploads a fourth model should find out that their first is
                    # gone from the answer, not from the switcher.
                    "replaced": evicted,
                    "held": len(uploads.for_session(session_id)),
                },
                session_id=session_id,
            )

        def _refuse_upload(
            self, status: HTTPStatus, message: str, retry_after: int = 0
        ) -> None:
            """Answer an upload without reading its body, and end the connection.

            Every refusal here happens *before* the body is read -- that is the
            point of them, since the cheapest way to survive a 2GB upload is not
            to read it. But an unread body is still sitting in the socket, and
            on a connection that gets reused it would be parsed as the next
            request: the client would see a nonsense answer to a question it had
            not finished asking.

            ``http.server`` speaks HTTP/1.0 by default, so today every
            connection is closed after one exchange anyway and this cannot
            happen. That is a default, not a decision -- anyone setting
            ``protocol_version = "HTTP/1.1"`` for keep-alive would turn four
            polite refusals into a protocol desync -- so the close is stated
            here rather than inherited.
            """
            body = json.dumps({"error": message}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if retry_after:
                self.send_header("Retry-After", str(retry_after))
            self.send_header("Content-Length", str(len(body)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def _handle_forget(self) -> None:
            """Drop one of this session's uploaded models.

            Worth having for the same reason the upload is per-session: someone
            who puts a confidential model into a demo server should be able to
            take it out again without closing the browser and hoping.
            """
            payload = self._read_json()
            if payload is None:
                return
            name = str(payload.get("model", "") or "").strip()
            session_id = self._session_cookie() or ""
            if not name or not uploads.forget(session_id, name):
                # One answer for "no such model" and "not yours". Distinguishing
                # them would turn this into a way to ask whether a name exists
                # in somebody else's session.
                self._json(
                    HTTPStatus.NOT_FOUND,
                    {"error": "you have no uploaded model by that name"},
                )
                return
            self._json(HTTPStatus.OK, {"forgotten": name})

        def _read_json(self) -> dict | None:
            """The request body, or None once an error has been answered."""
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length == 0 or length > _MAX_BODY_BYTES:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "request body must be non-empty and under 10KB"},
                )
                return None
            try:
                payload = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "malformed JSON body"})
                return None
            if not isinstance(payload, dict):
                self._json(
                    HTTPStatus.BAD_REQUEST, {"error": "request body must be an object"}
                )
                return None
            return payload

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
                "Set-Cookie", f"{_SESSION_COOKIE}={session_id}; Path=/; HttpOnly; SameSite=Lax{self._secure_cookie()}"
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
            self.person = ""
            self.verified = False
            self.credential = ""
            if not access_token and users is None and auth0 is None:
                return True

            supplied = ""
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                supplied = header[len("Bearer ") :].strip()
            if not supplied:
                supplied = (params.get("token") or [""])[0]
            if not supplied:
                supplied = self._cookie(_TOKEN_COOKIE) or ""

            # Auth0 first. A JWT is self-describing -- three dot-separated
            # segments -- so trying it against the personal-token directory
            # first would mean comparing a 900-byte string against every entry
            # for nothing.
            if auth0 is not None and supplied.count(".") == 2:
                try:
                    identity = auth0.verify(supplied)
                except Auth0Error:
                    # Not fatal on its own: a personal token may be configured
                    # alongside. The refusal below covers it if not.
                    pass
                else:
                    self.person = identity.label
                    self.verified = True
                    self.credential = supplied
                    return True

            # A personal token is tried next and, when it resolves, is the
            # answer: it both admits the request and names who made it. The
            # shared token still works alongside, and grants access without a
            # name -- which is exactly how it is then recorded.
            if users is not None and supplied:
                person = users.resolve(supplied)
                if person:
                    self.person = person
                    self.verified = True
                    self.credential = supplied
                    return True

            if access_token and supplied and secrets.compare_digest(supplied, access_token):
                self.credential = supplied
                return True

            # A browser asking for a page gets a page. Answering the document
            # request with JSON put a wall of `{"error": ...}` on screen where a
            # sign-in form belonged -- correct for the API, useless as a website,
            # and the reason this exists.
            if self.command == "GET" and _wants_html(self.headers.get("Accept", "")):
                self._serve_sign_in()
                return False

            self._json(
                HTTPStatus.UNAUTHORIZED,
                {
                    "error": "This server requires you to sign in.",
                    "how": (
                        "Sign in through Auth0 and send the access token as an "
                        "Authorization: Bearer header."
                        if auth0 is not None
                        else "Use your personal token from the user file this "
                        "server was started with, as ?token= or an "
                        "Authorization: Bearer header."
                        if users is not None
                        else "Open the link printed by `concordance serve`, which "
                        "carries the token, or send it as an Authorization: Bearer "
                        "header."
                    ),
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
                {"error": f"no such route: {self.path}", "routes": list(served_routes())},
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
    users=None,
    auth0=None,
    accepts_uploads: bool = True,
) -> None:
    """Run the chat server until interrupted."""
    handler, _ = make_handler(
        graph,
        provider,
        context,
        access_token=access_token,
        users=users,
        auth0=auth0,
        accepts_uploads=accepts_uploads,
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    base = f"http://{host}:{httpd.server_port}/"
    # The printed link carries the token, so the person who started the server
    # can simply click it. Anyone else has to be given it deliberately, which is
    # the entire point.
    url = f"{base}?token={access_token}" if access_token else base
    print(f"Concordance — {graph.model.name!r} — {url}")
    if not serves_full_interface():
        # Said out loud rather than left to be discovered. Someone looking at
        # the chat-only page has no way to know a fuller interface exists.
        print(
            "  serving the chat-only page: the built interface is missing. "
            "Run `npm --prefix frontend run build:embedded` for all six views."
        )
    if auth0 is not None:
        print(f"  sign-in via Auth0 — tenant {auth0.domain}, audience {auth0.audience}")
        print("  review decisions will record the verified Auth0 identity")
    if users is not None:
        # Named, because the difference between "someone signed this off" and
        # "Anna signed this off" is the whole reason the flag exists, and
        # nothing else on screen would reveal which one is in force.
        print(
            f"  {len(users)} reviewer(s) identified by personal token: "
            f"{', '.join(users.names)}"
        )
        print("  review decisions will record the authenticated name, not a claim")
    if accepts_uploads:
        # Named because it is on by default, and because "this server will
        # parse files strangers post to it" is a thing an operator should learn
        # from the banner rather than from a log line six weeks later.
        print(
            f"  visitors may upload a .pbix or a zipped .pbip "
            f"(up to {upload.MAX_UPLOAD_BYTES // (1024 * 1024)}MB, "
            f"held in memory for their own session only) — --no-upload turns this off"
        )
    if access_token:
        print("  access token required — share the link above to grant access")
    elif users is None and host not in ("127.0.0.1", "localhost", "::1"):
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
        # Named explicitly. Serving several models writes one log per model,
        # so the file that appears is not the path that was passed -- printing
        # the derived names is the difference between that being a design
        # decision and it being a surprise.
        logs = sorted(
            {str(c.decisions) for c in registry.contexts.values() if c.decisions}
        )
        if logs:
            print(f"  review decisions -> {', '.join(logs)}")
    # `base`, not `url`: the latter may carry ?token=, which would splice the
    # query string into the middle of the path and print a nonsense address.
    print(f"  api: {len(served_routes())} read-only endpoints under {base}api/")
    print("Press Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()

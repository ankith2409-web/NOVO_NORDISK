"""The local web chat server.

Two layers, tested differently on purpose. Session isolation is a plain unit
test against ``SessionStore`` -- no socket, no server thread, deterministic.
Routing and status codes are tested against a real ``ThreadingHTTPServer`` on
an ephemeral port, because that is the only way to know the HTTP layer itself
(headers, status codes, cookie round-trips) behaves as intended.
"""

from __future__ import annotations

import http.client
import json
import threading
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.agent.chat import ModelChat
from concordance.graph.csg import SemanticGraph
from concordance.llm.fake import FakeProvider, calls_tool, says
from concordance.web.server import SessionStore, make_handler

MODEL = Path("data/models/ClinicalTrialSafety.SemanticModel")


def _available() -> Path:
    if not MODEL.exists():
        pytest.skip(f"TMDL model not present: {MODEL}")
    return MODEL


@pytest.fixture(scope="module")
def graph() -> SemanticGraph:
    return SemanticGraph(TmdlAdapter().extract(str(_available())))


# -- session isolation, no server involved -----------------------------------

def test_two_unknown_ids_get_two_independent_chats(graph: SemanticGraph) -> None:
    made = []

    def factory() -> ModelChat:
        chat = ModelChat(graph, FakeProvider(script=[says("ok")]))
        made.append(chat)
        return chat

    store = SessionStore(factory)
    id_a, chat_a = store.get(None)
    id_b, chat_b = store.get(None)

    assert id_a != id_b
    assert chat_a is not chat_b
    assert len(made) == 2


def test_a_known_id_returns_the_same_chat(graph: SemanticGraph) -> None:
    store = SessionStore(lambda: ModelChat(graph, FakeProvider(script=[])))
    session_id, chat = store.get(None)

    again_id, again_chat = store.get(session_id)
    assert again_id == session_id
    assert again_chat is chat


def test_conversation_history_does_not_bleed_between_sessions(graph: SemanticGraph) -> None:
    store = SessionStore(
        lambda: ModelChat(graph, FakeProvider(script=[says("a"), says("b"), says("c")]))
    )
    id_a, chat_a = store.get(None)
    id_b, chat_b = store.get(None)

    chat_a.ask("first question")
    chat_a.ask("second question")
    chat_b.ask("only question")

    # Each ask() appends a user turn and a model turn to that chat's own history.
    assert len(chat_a.history) == 4
    assert len(chat_b.history) == 2
    assert store.get(id_a)[1] is chat_a
    assert store.get(id_b)[1] is chat_b


def test_an_unknown_id_is_not_mistaken_for_a_known_one(graph: SemanticGraph) -> None:
    store = SessionStore(lambda: ModelChat(graph, FakeProvider(script=[])))
    real_id, real_chat = store.get(None)

    _, other_chat = store.get("not-a-real-session-id")
    assert other_chat is not real_chat
    assert len(store) == 2


# -- the HTTP layer, against a real running server ---------------------------

class _RunningServer:
    def __init__(self, graph: SemanticGraph, script: list) -> None:
        handler, self.sessions = make_handler(graph, FakeProvider(script=script))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(graph: SemanticGraph):
    running = _RunningServer(graph, script=[says("A grounded answer.")])
    yield running
    running.close()


def test_root_serves_the_page_and_sets_a_session_cookie(server: _RunningServer) -> None:
    conn = server.connection()
    conn.request("GET", "/")
    response = conn.getresponse()
    body = response.read().decode()

    assert response.status == 200
    assert "text/html" in response.getheader("Content-Type")
    assert "ClinicalTrialSafety" in body

    cookie = SimpleCookie()
    cookie.load(response.getheader("Set-Cookie"))
    assert "concordance_session" in cookie


def test_unknown_route_is_404(server: _RunningServer) -> None:
    conn = server.connection()
    conn.request("GET", "/nonexistent")
    response = conn.getresponse()
    assert response.status == 404
    body = json.loads(response.read())
    assert "error" in body


def test_ask_with_empty_body_is_400(server: _RunningServer) -> None:
    conn = server.connection()
    conn.request("POST", "/api/ask", body=b"", headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    assert response.status == 400
    response.read()


def test_ask_with_malformed_json_is_400(server: _RunningServer) -> None:
    conn = server.connection()
    body = b"{not valid json"
    conn.request(
        "POST", "/api/ask", body=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    response = conn.getresponse()
    assert response.status == 400
    payload = json.loads(response.read())
    assert "malformed" in payload["error"]


def test_ask_with_blank_question_is_400(server: _RunningServer) -> None:
    conn = server.connection()
    body = json.dumps({"question": "   "}).encode()
    conn.request(
        "POST", "/api/ask", body=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    response = conn.getresponse()
    assert response.status == 400
    response.read()


def test_oversized_body_is_rejected_without_reading_it(server: _RunningServer) -> None:
    conn = server.connection()
    conn.request(
        "POST", "/api/ask", body=b"x",
        headers={"Content-Type": "application/json", "Content-Length": "999999"},
    )
    response = conn.getresponse()
    assert response.status == 400
    response.read()


def test_ask_returns_the_grounded_answer_and_tool_calls() -> None:
    from concordance.adapters.tmdl import TmdlAdapter as _TA

    graph = SemanticGraph(_TA().extract(str(_available())))
    server = _RunningServer(
        graph,
        script=[
            calls_tool("describe_measure", {"name": "Serious Adverse Events"}),
            says("It filters to Serious events."),
        ],
    )
    try:
        conn = server.connection()
        body = json.dumps({"question": "How is it calculated?"}).encode()
        conn.request(
            "POST", "/api/ask", body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        response = conn.getresponse()
        payload = json.loads(response.read())

        assert response.status == 200
        assert payload["grounded"] is True
        assert payload["tool_calls"] == [
            {"name": "describe_measure", "arguments": {"name": "Serious Adverse Events"}}
        ]
        assert "Serious" in payload["answer"]
    finally:
        server.close()


def test_cookie_from_root_keeps_the_same_session_across_requests(
    server: _RunningServer,
) -> None:
    conn = server.connection()
    conn.request("GET", "/")
    first = conn.getresponse()
    first.read()
    cookie_header = first.getheader("Set-Cookie").split(";")[0]

    conn2 = server.connection()
    conn2.request("GET", "/", headers={"Cookie": cookie_header})
    second = conn2.getresponse()
    second.read()

    assert second.getheader("Set-Cookie").split(";")[0] == cookie_header
    assert len(server.sessions) == 1


def test_no_cookie_creates_a_fresh_session_each_time(server: _RunningServer) -> None:
    for _ in range(3):
        conn = server.connection()
        conn.request("GET", "/")
        conn.getresponse().read()
    assert len(server.sessions) == 3


def test_overview_reflects_the_real_model(server: _RunningServer) -> None:
    conn = server.connection()
    conn.request("GET", "/api/overview")
    response = conn.getresponse()
    payload = json.loads(response.read())
    assert payload["measures"] == 24
    assert payload["model"] == "ClinicalTrialSafety"

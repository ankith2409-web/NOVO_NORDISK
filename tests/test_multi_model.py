"""Serving several models from one server.

A team does not own one semantic model, and the previous shape -- one process
per model, one port each -- pushed that arithmetic onto whoever was demoing.
The registry lets ``serve`` take any number of sources and route each request
to the one it names.

The failure this file exists to prevent is the quiet one. A request naming a
model the server does not hold, or a chat asked about model B while holding a
conversation grown from model A, must not be answered out of the default model
instead: that produces a confident, fluent, wrong answer, which is worse than
a refusal and far harder to notice.
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
from concordance.web.server import _capabilities_of, make_handler

MODELS = {
    "QualityControl": Path("data/models/QualityControl.SemanticModel"),
    "ClinicalTrialSafety": Path("data/models/ClinicalTrialSafety.SemanticModel"),
}


@pytest.fixture(scope="module")
def graphs() -> dict[str, SemanticGraph]:
    missing = [str(p) for p in MODELS.values() if not p.exists()]
    if missing:
        pytest.skip(f"models not present: {', '.join(missing)}")
    return {
        name: SemanticGraph(TmdlAdapter().extract(str(path)))
        for name, path in MODELS.items()
    }


@pytest.fixture
def registry(graphs: dict[str, SemanticGraph]) -> api.ModelRegistry:
    return api.ModelRegistry(
        contexts={name: api.ApiContext(graph=g) for name, g in graphs.items()},
        default="QualityControl",
    )


# -- routing, without a socket -------------------------------------------------

def test_a_named_model_is_the_one_answered_for(registry: api.ModelRegistry) -> None:
    status, payload = api.handle(registry, "/api/overview", {"model": ["ClinicalTrialSafety"]})
    assert status == 200
    assert payload["model"] == "ClinicalTrialSafety"


def test_naming_nothing_answers_for_the_default(registry: api.ModelRegistry) -> None:
    _, payload = api.handle(registry, "/api/overview", {})
    assert payload["model"] == "QualityControl"


def test_an_unloaded_model_is_refused_rather_than_substituted(
    registry: api.ModelRegistry,
) -> None:
    """The whole point. Falling back to the default here would answer a
    question nobody asked, in the voice of a question they did."""
    status, payload = api.handle(registry, "/api/overview", {"model": ["Nope"]})
    assert status == 404
    assert payload["loaded"] == ["ClinicalTrialSafety", "QualityControl"]
    assert "Nope" not in json.dumps(payload), "the error echoes caller input back"


def test_a_single_model_is_a_registry_of_one(graphs: dict[str, SemanticGraph]) -> None:
    """So there is one code path, not a special case for the common setup."""
    one = api.ModelRegistry.of(api.ApiContext(graph=graphs["QualityControl"]))
    assert list(one.contexts) == ["QualityControl"]
    assert one.default == "QualityControl"
    _, payload = api.handle(one, "/api/overview", {})
    assert payload["model"] == "QualityControl"


def test_every_read_route_honours_the_model_parameter(
    registry: api.ModelRegistry,
) -> None:
    """A switcher that moves five views and leaves the sixth on the old model
    is a bug that looks like a rendering glitch."""
    for route in sorted(api.ROUTES):
        status, _ = api.handle(registry, route, {"model": ["Nope"]})
        assert status == 404, f"{route} ignored ?model= and answered anyway"


# -- what the models endpoint reports -----------------------------------------

def test_the_models_endpoint_lists_what_is_loaded(registry: api.ModelRegistry) -> None:
    status, payload = api.handle(registry, "/api/models", {})
    assert status == 200
    assert payload["default"] == "QualityControl"
    assert [m["name"] for m in payload["models"]] == [
        "ClinicalTrialSafety",
        "QualityControl",
    ]


def test_the_listing_carries_real_counts_not_placeholders(
    registry: api.ModelRegistry, graphs: dict[str, SemanticGraph]
) -> None:
    _, payload = api.handle(registry, "/api/models", {})
    listed = {m["name"]: m for m in payload["models"]}
    for name, graph in graphs.items():
        assert listed[name]["measures"] == len(graph.model.measures)
        assert listed[name]["tables"] == len(graph.model.user_tables())


def test_capabilities_are_reported_per_model(
    graphs: dict[str, SemanticGraph],
) -> None:
    """Drift is configured per source, so one model having a comparison
    baseline says nothing about the others. Reporting it registry-wide would
    offer the interface a button that cannot work."""
    registry = api.ModelRegistry(
        contexts={
            "QualityControl": api.ApiContext(graph=graphs["QualityControl"]),
            "ClinicalTrialSafety": api.ApiContext(
                graph=graphs["ClinicalTrialSafety"],
                compare_to=graphs["QualityControl"],
            ),
        },
        default="QualityControl",
    )
    listed = {m["name"]: m["capabilities"] for m in registry.describe()["models"]}
    assert listed["QualityControl"] == {"drift": False}
    assert listed["ClinicalTrialSafety"] == {"drift": True}


def test_the_banner_names_the_same_capabilities_the_api_does(
    graphs: dict[str, SemanticGraph],
) -> None:
    """The startup banner is how someone finds out a flag they passed did not
    take effect, so it has to be derived from the context, not restated."""
    assert _capabilities_of(api.ApiContext(graph=graphs["QualityControl"])) == []
    assert _capabilities_of(
        api.ApiContext(
            graph=graphs["QualityControl"],
            compare_to=graphs["ClinicalTrialSafety"],
        )
    ) == ["drift"]


# -- over HTTP, including the chat --------------------------------------------

class Running:
    def __init__(self, registry: api.ModelRegistry, script: list) -> None:
        handler, self.sessions = make_handler(
            registry.contexts[registry.default].graph,
            FakeProvider(script=script),
            registry,
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def get(self, path: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        return conn.getresponse()

    def ask(self, body: dict, cookie: str | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = f"concordance_session={cookie}"
        conn.request("POST", "/api/ask", body=json.dumps(body), headers=headers)
        return conn.getresponse()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(registry: api.ModelRegistry):
    running = Running(registry, [says("ok")] * 6)
    yield running
    running.close()


def test_the_models_endpoint_is_reachable(server: Running) -> None:
    response = server.get("/api/models")
    assert response.status == 200
    assert len(json.loads(response.read())["models"]) == 2


def test_switching_model_over_http_switches_the_answer(server: Running) -> None:
    for name in ("ClinicalTrialSafety", "QualityControl"):
        body = json.loads(server.get(f"/api/overview?model={name}").read())
        assert body["model"] == name


def test_the_chat_is_bound_to_the_model_it_was_asked_about(
    server: Running, graphs: dict[str, SemanticGraph]
) -> None:
    """The chat reads tables and DAX out of a graph. Asking about one model and
    reading another's is the same silent wrongness as the API case."""
    server.ask({"question": "hi", "model": "ClinicalTrialSafety"})
    chats = [chat for chat in server.sessions._sessions.values()]
    assert len(chats) == 1
    assert chats[0].tools.graph is graphs["ClinicalTrialSafety"]


def test_one_browser_holds_a_separate_conversation_per_model(
    server: Running,
) -> None:
    """Switching models must not replay the previous model's tool results into
    the next question -- and switching back must not lose the thread."""
    first = server.ask({"question": "one", "model": "QualityControl"})
    cookie = first.getheader("Set-Cookie").split("=")[1].split(";")[0]
    first.read()

    server.ask({"question": "two", "model": "ClinicalTrialSafety"}, cookie).read()
    server.ask({"question": "three", "model": "QualityControl"}, cookie).read()

    by_model = {
        model: chat for (_, model), chat in server.sessions._sessions.items()
    }
    assert set(by_model) == {"QualityControl", "ClinicalTrialSafety"}
    # Two turns each way for QualityControl's two questions, one for the other.
    assert len(by_model["QualityControl"].history) == 4
    assert len(by_model["ClinicalTrialSafety"].history) == 2


def test_omitting_the_model_shares_the_defaults_conversation(
    server: Running,
) -> None:
    """Otherwise the interface forks into two histories the moment it starts
    sending an explicit name for what it was already showing."""
    first = server.ask({"question": "one"})
    cookie = first.getheader("Set-Cookie").split("=")[1].split(";")[0]
    first.read()
    server.ask({"question": "two", "model": "QualityControl"}, cookie).read()

    assert len(server.sessions._sessions) == 1


def test_asking_an_unloaded_model_is_refused_and_spends_nothing(
    server: Running,
) -> None:
    response = server.ask({"question": "hi", "model": "Nope"})
    assert response.status == 404
    assert len(server.sessions._sessions) == 0, "a refused ask still minted a chat"

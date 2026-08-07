"""Growth that must stay bounded, and the CLI paths that reach it.

Every case here is a bug that survived the existing suite because the tests
exercised library functions directly while the failure lived one layer up, in
the command line, or only appeared over time rather than on the first call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.agent.chat import ModelChat
from concordance.graph.csg import SemanticGraph
from concordance.llm.fake import FakeProvider, calls_tool, says
from concordance.web.server import SessionStore

MODEL = Path("data/models/ClinicalTrialSafety.SemanticModel")


@pytest.fixture(scope="module")
def graph() -> SemanticGraph:
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return SemanticGraph(TmdlAdapter().extract(str(MODEL)))


# -- conversation history ----------------------------------------------------

def test_history_stops_growing_once_the_limit_is_reached(graph: SemanticGraph) -> None:
    """History is resent in full each request, so unbounded growth costs tokens."""
    provider = FakeProvider(script=[says(f"answer {i}") for i in range(60)])
    chat = ModelChat(graph, provider, max_history=3)

    for i in range(20):
        chat.ask(f"question {i}")

    user_turns = [m for m in chat.history if m.role == "user"]
    assert len(user_turns) <= 3 + 1  # the trimmed window, plus the current turn


def test_trimming_keeps_the_most_recent_exchanges(graph: SemanticGraph) -> None:
    provider = FakeProvider(script=[says(f"answer {i}") for i in range(30)])
    chat = ModelChat(graph, provider, max_history=2)

    for i in range(6):
        chat.ask(f"question {i}")

    questions = [m.text for m in chat.history if m.role == "user"]
    assert "question 5" in questions
    assert "question 0" not in questions


def test_trimming_never_splits_a_tool_call_from_its_result(graph: SemanticGraph) -> None:
    """The constraint that makes this non-trivial.

    Gemini rejects a conversation where a functionCall appears without the
    functionResponse answering it, so a cut at an arbitrary index would break
    the next request rather than merely shortening the payload.
    """
    script = []
    for i in range(12):
        script.append(calls_tool("overview"))
        script.append(says(f"answer {i}"))
    chat = ModelChat(graph, FakeProvider(script=script), max_history=2)

    for i in range(6):
        chat.ask(f"question {i}")

    # Every tool turn must be preceded by a model turn that requested a call.
    for index, message in enumerate(chat.history):
        if message.role == "tool":
            assert index > 0, "history begins with an orphaned tool result"
            previous = chat.history[index - 1]
            assert previous.role == "model" and previous.tool_calls

    # And the history must start at a user turn, never mid-exchange.
    assert chat.history[0].role == "user"


def test_history_is_untouched_below_the_limit(graph: SemanticGraph) -> None:
    provider = FakeProvider(script=[says("a"), says("b")])
    chat = ModelChat(graph, provider, max_history=10)

    chat.ask("one")
    chat.ask("two")

    assert [m.text for m in chat.history if m.role == "user"] == ["one", "two"]


# -- session store -----------------------------------------------------------

def test_session_count_is_capped(graph: SemanticGraph) -> None:
    """Every cookieless request mints a session; without a cap this grows forever."""
    store = SessionStore(
        lambda: ModelChat(graph, FakeProvider(script=[])), max_sessions=5
    )
    for _ in range(50):
        store.get(None)

    assert len(store) == 5


def test_eviction_drops_the_least_recently_used(graph: SemanticGraph) -> None:
    store = SessionStore(
        lambda: ModelChat(graph, FakeProvider(script=[])), max_sessions=3
    )
    first, _ = store.get(None)
    second, _ = store.get(None)
    third, _ = store.get(None)

    # Touching `first` makes `second` the least recently used.
    store.get(first)
    fourth, _ = store.get(None)

    assert len(store) == 3
    # `first` survived because it was used recently; `second` was evicted.
    assert store.get(first)[0] == first
    assert store.get(second)[0] != second
    assert store.get(fourth)[0] == fourth


def test_an_evicted_visitor_gets_a_fresh_session_rather_than_an_error(
    graph: SemanticGraph,
) -> None:
    """Being forgotten is the right failure for a chat with no durable state."""
    store = SessionStore(
        lambda: ModelChat(graph, FakeProvider(script=[])), max_sessions=1
    )
    old_id, old_chat = store.get(None)
    store.get(None)  # evicts old_id

    new_id, new_chat = store.get(old_id)
    assert new_id != old_id
    assert new_chat is not old_chat


# -- the CLI path that crashed ------------------------------------------------

def test_drift_accepts_a_snapshot_file_on_either_side(tmp_path: Path) -> None:
    """Snapshot now, compare later -- the primary real-world drift workflow.

    This crashed with AttributeError because the CLI called a module-level
    `snapshot.load` that does not exist; the function lives on the Snapshot
    class. Every drift test called compare() directly, so nothing reached it.
    """
    from concordance.cli import _as_snapshot
    from concordance.drift import snapshot as snap

    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")

    graph = SemanticGraph(TmdlAdapter().extract(str(MODEL)))
    path = snap.take(graph, "v1").save(tmp_path / "v1.json")

    loaded, loaded_graph = _as_snapshot(str(path), "")
    assert loaded.model_name == "ClinicalTrialSafety"
    assert loaded_graph is None  # a file yields no live graph

    taken, taken_graph = _as_snapshot(str(MODEL), "")
    assert taken.model_name == "ClinicalTrialSafety"
    assert taken_graph is not None
    assert taken.fingerprints == loaded.fingerprints


def test_a_model_snapshotted_on_the_fly_is_labelled_usefully() -> None:
    """"before -> after" in a report header says nothing about what was compared."""
    from concordance.cli import _as_snapshot

    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")

    taken, _ = _as_snapshot(str(MODEL), "")
    assert taken.label == MODEL.name

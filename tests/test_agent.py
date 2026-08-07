"""The tool surface and the conversation loop.

Runs entirely against a scripted provider -- no network, no API key, no quota.
What is tested here is the loop's own behaviour: that it grounds answers in the
graph, that it rejects tools it never declared, and that it terminates.

The rejection test exists because of something observed from the real API
rather than imagined: given a narrow tool surface and a question it did not fit,
Gemini called a ``list_measures`` function that had never been declared. A loop
that dispatches on a name without checking it would have raised KeyError on a
perfectly ordinary question.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.agent.chat import ModelChat
from concordance.agent.tools import ModelTools
from concordance.graph.csg import SemanticGraph
from concordance.llm.base import Completion, ToolCall
from concordance.llm.fake import FakeProvider, calls_tool, says

MODEL = Path("data/models/ClinicalTrialSafety.SemanticModel")


@pytest.fixture(scope="module")
def graph() -> SemanticGraph:
    if not MODEL.exists():
        pytest.skip(f"TMDL model not present: {MODEL}")
    return SemanticGraph(TmdlAdapter().extract(str(MODEL)))


@pytest.fixture
def tools(graph: SemanticGraph) -> ModelTools:
    return ModelTools(graph)


# -- the tools themselves ----------------------------------------------------

def test_every_declared_tool_has_a_handler(tools: ModelTools) -> None:
    """A declared tool the dispatcher cannot run would fail only at runtime."""
    for spec in tools.specs():
        assert tools.dispatch(spec.name, _minimal_arguments(spec)) is not None


def _minimal_arguments(spec) -> dict:
    required = spec.parameters.get("required", [])
    return {name: "x" for name in required}


def test_overview_reports_what_could_not_be_extracted(tools: ModelTools) -> None:
    result = tools.overview()
    assert result["measures"] == 24
    assert "unresolved_references" in result
    assert "not_extracted" in result


def test_describe_measure_returns_the_real_expression(tools: ModelTools) -> None:
    result = tools.describe_measure("Serious Adverse Events")
    assert 'AdverseEvent[Seriousness] = "Serious"' in result["expression"]
    assert any("Adverse Event Count" in d for d in result["depends_on"])
    assert any("SAE Rate per 100 Patients" in u for u in result["used_by"])


def test_a_missing_measure_returns_an_error_not_an_invention(tools: ModelTools) -> None:
    result = tools.describe_measure("Patient Mortality Index")
    assert "error" in result
    assert "expression" not in result
    assert "did_you_mean" in result


def test_qualified_measure_names_are_accepted(tools: ModelTools) -> None:
    result = tools.describe_measure("Clinical Metrics[Patients Enrolled]")
    assert result.get("name") == "Patients Enrolled"


def test_what_uses_traces_impact_through_the_graph(tools: ModelTools) -> None:
    result = tools.what_uses("Patient")
    assert result["object"] == "table:Patient"
    assert any("Dropout Rate" in n for n in result["would_be_affected"])


def test_unknown_tool_is_answered_not_raised(tools: ModelTools) -> None:
    result = tools.dispatch("list_measures_by_vibes", {})
    assert "error" in result
    assert "available_tools" in result


def test_wrong_arguments_are_answered_not_raised(tools: ModelTools) -> None:
    result = tools.dispatch("describe_measure", {"wrong_argument": "x"})
    assert "error" in result
    assert "expected" in result


# -- the conversation loop ---------------------------------------------------

def test_answer_is_grounded_in_a_tool_call(graph: SemanticGraph) -> None:
    provider = FakeProvider(
        script=[
            calls_tool("describe_measure", {"name": "Serious Adverse Events"}),
            says("It filters adverse events to those marked Serious."),
        ]
    )
    exchange = ModelChat(graph, provider).ask("How is that measure calculated?")

    assert exchange.grounded
    assert exchange.tool_calls == [
        ("describe_measure", {"name": "Serious Adverse Events"})
    ]
    assert "Serious" in exchange.answer


def test_a_tool_that_was_never_declared_is_rejected(graph: SemanticGraph) -> None:
    """Observed from the real API, not hypothetical -- see the module docstring."""
    provider = FakeProvider(
        script=[
            Completion(
                text="",
                tool_calls=(ToolCall(name="list_measures_by_vibes", arguments={}),),
            ),
            says("Sorry, I could not look that up."),
        ]
    )
    exchange = ModelChat(graph, provider).ask("Which measure changed most?")

    assert exchange.rejected_calls == ["list_measures_by_vibes"]
    assert exchange.tool_calls == []
    assert not exchange.grounded
    # The loop must still produce an answer rather than crashing.
    assert exchange.answer


def test_the_rejection_is_reported_back_to_the_model(graph: SemanticGraph) -> None:
    """So it can recover on the next turn instead of repeating the mistake."""
    provider = FakeProvider(
        script=[
            Completion(text="", tool_calls=(ToolCall(name="nope", arguments={}),)),
            says("done"),
        ]
    )
    ModelChat(graph, provider).ask("anything")

    second_call = provider.calls[1]["messages"]
    tool_replies = [m for m in second_call if m.role == "tool"]
    assert tool_replies
    assert "error" in tool_replies[-1].tool_result
    assert "available_tools" in tool_replies[-1].tool_result


def test_an_answer_without_tools_is_marked_ungrounded(graph: SemanticGraph) -> None:
    provider = FakeProvider(script=[says("Paris is the capital of France.")])
    exchange = ModelChat(graph, provider).ask("What is the capital of France?")

    assert not exchange.grounded
    assert exchange.tool_calls == []


def test_the_loop_terminates_when_the_model_keeps_calling_tools(
    graph: SemanticGraph,
) -> None:
    """Without a bound this would run until the quota or the process died."""
    provider = FakeProvider(script=[calls_tool("overview") for _ in range(50)])
    chat = ModelChat(graph, provider, max_rounds=3)
    exchange = chat.ask("go round forever")

    assert len(exchange.tool_calls) == 3
    assert exchange.answer  # a final answer is still produced


def test_the_system_prompt_names_the_model_under_discussion(
    graph: SemanticGraph,
) -> None:
    provider = FakeProvider(script=[says("ok")])
    ModelChat(graph, provider).ask("hello")

    system = provider.calls[0]["system"]
    assert "ClinicalTrialSafety" in system
    assert "Never invent" in system


def test_tools_are_offered_on_the_first_turn(graph: SemanticGraph) -> None:
    provider = FakeProvider(script=[says("ok")])
    ModelChat(graph, provider).ask("hello")

    offered = provider.calls[0]["tools"]
    assert "describe_measure" in offered
    assert "what_uses" in offered


def test_temperature_is_zero_so_answers_are_reproducible(graph: SemanticGraph) -> None:
    provider = FakeProvider(script=[says("ok")])
    ModelChat(graph, provider).ask("hello")
    assert provider.calls[0]["temperature"] == 0.0

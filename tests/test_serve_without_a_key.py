"""Starting the server with no language model configured.

The server used to exit rather than start. That is the right rule for `ask`,
which *is* the chat, and the wrong rule for `serve`, where the chat is one
collapsible panel beside eight pages that ask a model nothing -- every figure
they show is computed by running SQL against the file's own rows, on purpose,
so that it can be checked rather than trusted.

Somebody who cloned the repository to see what it does got an error message and
no program, over a feature they had not asked to use.
"""

from __future__ import annotations

import pytest

from concordance.llm.base import LlmError
from concordance.llm.unconfigured import UnconfiguredProvider


def test_it_answers_a_question_by_saying_what_is_missing() -> None:
    provider = UnconfiguredProvider(
        reasons=["  gemini (No Gemini API key…)", "  groq (No Groq API key…)"]
    )
    with pytest.raises(LlmError) as raised:
        provider.complete([])

    said = str(raised.value)
    # What is wrong, what still works, and which keys were looked for.
    assert "without a language model" in said
    assert "does not need one" in said
    assert "gemini" in said and "groq" in said


def test_it_says_the_rest_of_the_page_is_unaffected() -> None:
    """The half a reader needs most: the figures on screen are not degraded.

    They were never asked of a model in the first place.
    """
    assert "read from the file itself" in UnconfiguredProvider().explanation


def test_the_chat_route_turns_it_into_a_reply_rather_than_a_crash() -> None:
    """`LlmError` is what the server already catches and returns as a 502.

    So this provider needs no special handling anywhere -- which is the point
    of raising that type rather than inventing one.
    """
    provider = UnconfiguredProvider()
    with pytest.raises(LlmError):
        provider.complete([], system="anything", tools=None)


def test_a_server_starts_without_a_provider_and_serves_every_route() -> None:
    """The whole point, end to end: no key, and the interface still works."""
    from pathlib import Path

    from concordance.adapters.pbix import PbixAdapter
    from concordance.graph.csg import SemanticGraph
    from concordance.web import api
    from concordance.web.server import make_handler

    path = Path("data/models/StoreSales.pbix")
    if not path.exists():
        pytest.skip(f"model not present: {path}")

    graph = SemanticGraph(PbixAdapter().extract(str(path)))
    context = api.ApiContext(graph=graph)
    handler, _ = make_handler(graph, UnconfiguredProvider(), api.ModelRegistry.of(context))
    assert handler is not None

    for route in (
        "/api/overview", "/api/values", "/api/dashboard", "/api/requirements",
        "/api/dataset", "/api/report", "/api/graph", "/api/review",
    ):
        status, _body = api.handle(context, route, {})
        assert status == 200, (route, status)


def test_warming_a_context_fills_its_caches() -> None:
    """The dashboard used to open on a grid of cards reading "computing…".

    The work has to happen; it does not have to happen while somebody watches.
    """
    from pathlib import Path

    from concordance.adapters.pbix import PbixAdapter
    from concordance.graph.csg import SemanticGraph
    from concordance.web import api

    path = Path("data/models/StoreSales.pbix")
    if not path.exists():
        pytest.skip(f"model not present: {path}")

    context = api.ApiContext(graph=SemanticGraph(PbixAdapter().extract(str(path))))
    assert context._evaluated is None
    context.warm()
    assert context._evaluated is not None
    assert context._evaluated.available


def test_warming_a_source_with_no_rows_does_not_raise() -> None:
    """A TMDL folder carries a schema and no data. Warming it must report that
    the same way a request would, not take the server down at startup."""
    from pathlib import Path

    from concordance.adapters.tmdl import TmdlAdapter
    from concordance.graph.csg import SemanticGraph
    from concordance.web import api

    path = Path("data/models/QualityControl.SemanticModel")
    if not path.exists():
        pytest.skip(f"model not present: {path}")

    context = api.ApiContext(graph=SemanticGraph(TmdlAdapter().extract(str(path))))
    context.warm()
    assert context._evaluated is not None
    assert not context._evaluated.available

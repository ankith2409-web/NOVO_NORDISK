"""The Anthropic provider's request and response translation.

No network call anywhere here -- these test the pure functions that build a
request body and parse a response, against payload shapes taken from
Anthropic's published Messages API. That is a deliberate substitute for an
integration test: the sandbox this project is built in cannot reach a live
Anthropic-compatible endpoint (its egress policy blocks the gateway this key
was issued for), so what can be verified from here is that the translation is
correct against the documented contract, not that a specific key or gateway
answers it. A live key should be exercised locally before relying on this in a
demo.

The tool-call id pairing gets the most attention. Anthropic pairs a
``tool_result`` to its ``tool_use`` strictly by id and rejects a request where
they do not match exactly -- unlike Gemini, which matches by function name and
tolerates no id at all. That pairing crosses two independently-built messages
(``agent/chat.py`` constructs the tool-result message well after the model's
turn is gone), so a bug here would not show up as a wrong answer; it would show
up as the very first tool-using question failing outright against a real key.
"""

from __future__ import annotations

import json

import pytest

from concordance.llm.anthropic import (
    DEFAULT_MODEL,
    AnthropicProvider,
    _to_completion,
    _to_messages,
)
from concordance.llm.base import LlmError, Message, ToolCall, ToolSpec


# -- building the request ------------------------------------------------

def test_a_plain_question_becomes_one_user_message() -> None:
    out = _to_messages([Message(role="user", text="What does OOS Rate measure?")])
    assert out == [
        {"role": "user", "content": [{"type": "text", "text": "What does OOS Rate measure?"}]}
    ]


def test_the_model_role_is_renamed_assistant() -> None:
    """Anthropic has no 'model' role; Gemini has no 'assistant' role."""
    out = _to_messages([Message(role="model", text="It is a ratio.")])
    assert out[0]["role"] == "assistant"


def test_a_tool_call_becomes_a_tool_use_block_carrying_its_real_id() -> None:
    call = ToolCall(name="describe_measure", arguments={"name": "OOS Rate"}, call_id="toolu_01")
    out = _to_messages([Message(role="model", tool_calls=(call,))])

    block = out[0]["content"][0]
    assert block == {
        "type": "tool_use",
        "id": "toolu_01",
        "name": "describe_measure",
        "input": {"name": "OOS Rate"},
    }


def test_a_tool_result_references_the_same_id_the_call_carried() -> None:
    """The pairing this whole module exists to get right.

    Anthropic rejects a request outright if a tool_result's id does not exactly
    match a tool_use id declared earlier in the conversation -- there is no
    fallback to matching by name. ``call_id`` has to survive from the model's
    turn, through ``agent/chat.py`` building a brand new Message for the
    result, to this serialisation, unchanged.
    """
    history = [
        Message(
            role="model",
            tool_calls=(ToolCall(name="overview", arguments={}, call_id="toolu_42"),),
        ),
        # What agent/chat.py actually constructs after running the tool --
        # see ModelChat._run.
        Message(
            role="tool",
            tool_name="overview",
            tool_call_id="toolu_42",
            tool_result={"tables": 6},
        ),
    ]
    out = _to_messages(history)

    tool_use_id = out[0]["content"][0]["id"]
    tool_result_id = out[1]["content"][0]["tool_use_id"]
    assert tool_use_id == tool_result_id == "toolu_42"


def test_a_tool_result_without_an_id_falls_back_to_the_name_on_both_sides() -> None:
    """A hand-built ToolCall in a test may carry no id. Both branches must agree
    on the same fallback, or the two messages silently stop referring to each
    other."""
    history = [
        Message(role="model", tool_calls=(ToolCall(name="overview", arguments={}),)),
        Message(role="tool", tool_name="overview", tool_result={}),
    ]
    out = _to_messages(history)
    assert out[0]["content"][0]["id"] == out[1]["content"][0]["tool_use_id"] == "overview"


def test_tool_result_content_is_a_string_not_an_object() -> None:
    """Unlike Gemini's function response, Anthropic's tool_result content is text."""
    out = _to_messages(
        [Message(role="tool", tool_name="overview", tool_result={"tables": 6})]
    )
    content = out[0]["content"][0]["content"]
    assert isinstance(content, str)
    assert json.loads(content) == {"tables": 6}


def test_tool_specs_become_input_schema_not_parameters() -> None:
    provider = AnthropicProvider(api_key="test-key")
    captured = {}

    def fake_post(payload):
        captured.update(payload)
        return {"content": [{"type": "text", "text": "ok"}]}

    provider._post = fake_post  # type: ignore[method-assign]
    provider.complete(
        [Message(role="user", text="hi")],
        tools=[ToolSpec("overview", "Summarise the model.", {"type": "object"})],
    )

    assert captured["tools"] == [
        {"name": "overview", "description": "Summarise the model.", "input_schema": {"type": "object"}}
    ]


def test_max_tokens_is_always_sent() -> None:
    """Anthropic requires it; there is no server-side default the way Gemini has."""
    provider = AnthropicProvider(api_key="test-key")
    captured = {}
    provider._post = lambda payload: (captured.update(payload) or {"content": []})  # type: ignore[method-assign]
    provider.complete([Message(role="user", text="hi")])
    assert isinstance(captured["max_tokens"], int) and captured["max_tokens"] > 0


# -- reading the response -------------------------------------------------

def test_a_text_response_is_read() -> None:
    completion = _to_completion(
        {"content": [{"type": "text", "text": "It divides failures by tests."}], "usage": {}},
        DEFAULT_MODEL,
    )
    assert completion.text == "It divides failures by tests."
    assert not completion.wants_tools


def test_a_tool_use_response_carries_its_id_forward() -> None:
    completion = _to_completion(
        {
            "content": [
                {"type": "tool_use", "id": "toolu_99", "name": "search", "input": {"text": "oos"}}
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
        DEFAULT_MODEL,
    )
    assert completion.wants_tools
    call = completion.tool_calls[0]
    assert call.name == "search"
    assert call.arguments == {"text": "oos"}
    assert call.call_id == "toolu_99"
    assert completion.usage == {"input_tokens": 10, "output_tokens": 5}


def test_text_and_tool_use_can_share_one_response() -> None:
    """Anthropic can narrate before it calls a tool, in the same turn."""
    completion = _to_completion(
        {
            "content": [
                {"type": "text", "text": "Let me check that."},
                {"type": "tool_use", "id": "toolu_1", "name": "overview", "input": {}},
            ]
        },
        DEFAULT_MODEL,
    )
    assert completion.text == "Let me check that."
    assert len(completion.tool_calls) == 1


def test_an_error_body_raises_rather_than_returning_empty() -> None:
    with pytest.raises(LlmError):
        _to_completion({"type": "error", "error": {"message": "overloaded"}}, DEFAULT_MODEL)


def test_only_integer_usage_fields_are_kept() -> None:
    """A malformed or future usage field must not crash accounting."""
    completion = _to_completion(
        {"content": [], "usage": {"input_tokens": 3, "note": "beta"}}, DEFAULT_MODEL
    )
    assert completion.usage == {"input_tokens": 3}


# -- configuration ----------------------------------------------------------

def test_a_missing_key_fails_fast_with_an_actionable_message(monkeypatch) -> None:
    # _key_from_environment also checks a .env path relative to the module
    # file, not to cwd, precisely so the CLI works from any invocation
    # directory -- the same as gemini.py. That means a chdir cannot isolate
    # this test from this project's own real .env; patching the lookup itself
    # is what actually exercises "no key found anywhere" rather than "no key
    # in this particular fake environment".
    monkeypatch.setattr("concordance.llm.anthropic._key_from_environment", lambda: None)
    with pytest.raises(LlmError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider(api_key=None)


def test_a_gateway_base_url_replaces_the_default_host() -> None:
    """A Claude-compatible gateway is a different host, same request shape --
    this is the whole reason base_url is configurable rather than hard-coded."""
    provider = AnthropicProvider(api_key="test-key", base_url="https://gw.example.com/")
    # Trailing slash is stripped so the path join never doubles a slash.
    assert provider._base_url == "https://gw.example.com"


def test_the_api_key_never_appears_in_a_raised_error() -> None:
    """An error body can echo request content back; the key must not survive that."""
    import io
    import urllib.error

    from concordance.llm.anthropic import _describe, _redact

    secret = "secret-value-123"
    body = json.dumps({"error": {"message": f"bad key: {secret}"}}).encode()
    error = urllib.error.HTTPError(
        url="https://api.anthropic.com/v1/messages",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body),
    )

    assert secret not in _redact(_describe(error), secret)

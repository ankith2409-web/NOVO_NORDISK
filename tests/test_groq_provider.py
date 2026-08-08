"""The Groq provider's request and response translation.

No network call anywhere here, for the same reason as the Anthropic provider's
tests: this sandbox's egress policy blocks ``api.groq.com`` outright (confirmed
via a direct CONNECT, answered with a policy 403), so what can be verified from
here is that the translation is correct against Groq's documented
OpenAI-compatible contract, not that a specific key answers it. Run one real,
free question locally before relying on this for a demo.

Two things this format gets uniquely wrong if copied carelessly from the other
two providers. Tool call arguments are a JSON-encoded *string* here, where
Anthropic and Gemini both take a nested object -- a provider that reused either
of those would send a payload Groq's API rejects outright. And the system
prompt is an ordinary message in the list, not a separate top-level field the
way Anthropic's API takes it.
"""

from __future__ import annotations

import json

import pytest

from concordance.llm.base import LlmError, Message, ToolCall, ToolSpec
from concordance.llm.groq import DEFAULT_MODEL, GroqProvider, _to_completion, _to_messages


# -- building the request ------------------------------------------------

def test_a_plain_question_becomes_one_user_message() -> None:
    out = _to_messages([Message(role="user", text="What does OOS Rate measure?")])
    assert out == [{"role": "user", "content": "What does OOS Rate measure?"}]


def test_the_model_role_is_renamed_assistant() -> None:
    out = _to_messages([Message(role="model", text="It is a ratio.")])
    assert out[0]["role"] == "assistant"


def test_tool_call_arguments_are_a_json_string_not_an_object() -> None:
    """The detail that breaks silently if copied from Anthropic or Gemini.

    Both of those APIs take a nested object for a tool call's arguments; this
    one requires the arguments pre-serialised into a JSON string inside the
    function block. Sending an object here is a shape Groq's API rejects, not
    a difference it tolerates.
    """
    call = ToolCall(name="describe_measure", arguments={"name": "OOS Rate"}, call_id="call_1")
    out = _to_messages([Message(role="model", tool_calls=(call,))])

    function = out[0]["tool_calls"][0]["function"]
    assert isinstance(function["arguments"], str)
    assert json.loads(function["arguments"]) == {"name": "OOS Rate"}


def test_a_tool_result_references_the_same_id_the_call_carried() -> None:
    """Paired by id, the same as Anthropic and unlike Gemini's match-by-name.

    ``tool_call_id`` has to survive from the model's turn, through
    ``agent/chat.py`` building a brand new Message for the result, to this
    serialisation, unchanged -- the same plumbing added for the Anthropic
    provider, reused here rather than duplicated.
    """
    history = [
        Message(
            role="model",
            tool_calls=(ToolCall(name="overview", arguments={}, call_id="call_42"),),
        ),
        Message(
            role="tool",
            tool_name="overview",
            tool_call_id="call_42",
            tool_result={"tables": 6},
        ),
    ]
    out = _to_messages(history)

    assistant_id = out[0]["tool_calls"][0]["id"]
    tool_result_id = out[1]["tool_call_id"]
    assert assistant_id == tool_result_id == "call_42"


def test_a_tool_result_without_an_id_falls_back_to_the_name_on_both_sides() -> None:
    history = [
        Message(role="model", tool_calls=(ToolCall(name="overview", arguments={}),)),
        Message(role="tool", tool_name="overview", tool_result={}),
    ]
    out = _to_messages(history)
    assert out[0]["tool_calls"][0]["id"] == out[1]["tool_call_id"] == "overview"


def test_the_system_prompt_is_a_message_not_a_top_level_field() -> None:
    """Unlike Anthropic's `system` parameter, this API wants it in the list."""
    provider = GroqProvider(api_key="test-key")
    captured = {}
    provider._post = lambda payload: (captured.update(payload) or {"choices": []})  # type: ignore[method-assign]
    with pytest.raises(LlmError):  # empty choices -- fine, only the request matters here
        provider.complete([Message(role="user", text="hi")], system="Be concise.")

    assert "system" not in captured
    assert captured["messages"][0] == {"role": "system", "content": "Be concise."}


def test_tool_specs_use_the_openai_function_wrapper() -> None:
    provider = GroqProvider(api_key="test-key")
    captured = {}

    def fake_post(payload):
        captured.update(payload)
        return {"choices": [{"message": {"content": "ok"}}]}

    provider._post = fake_post  # type: ignore[method-assign]
    provider.complete(
        [Message(role="user", text="hi")],
        tools=[ToolSpec("overview", "Summarise the model.", {"type": "object"})],
    )

    assert captured["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "overview",
                "description": "Summarise the model.",
                "parameters": {"type": "object"},
            },
        }
    ]


# -- reading the response -------------------------------------------------

def test_a_text_response_is_read() -> None:
    completion = _to_completion(
        {"choices": [{"message": {"content": "It divides failures by tests."}}]},
        DEFAULT_MODEL,
    )
    assert completion.text == "It divides failures by tests."
    assert not completion.wants_tools


def test_a_tool_call_response_parses_the_json_argument_string() -> None:
    completion = _to_completion(
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_99",
                                "type": "function",
                                "function": {
                                    "name": "search",
                                    "arguments": json.dumps({"text": "oos"}),
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
        DEFAULT_MODEL,
    )
    assert completion.wants_tools
    call = completion.tool_calls[0]
    assert call.name == "search"
    assert call.arguments == {"text": "oos"}
    assert call.call_id == "call_99"
    assert completion.usage == {"input_tokens": 10, "output_tokens": 5}


def test_a_malformed_argument_string_degrades_instead_of_crashing() -> None:
    """The model's own output, not this code's -- a parse error there should
    not take down the conversation."""
    completion = _to_completion(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {"name": "search", "arguments": "{not json"},
                            }
                        ]
                    }
                }
            ]
        },
        DEFAULT_MODEL,
    )
    assert completion.tool_calls[0].arguments == {}


def test_no_choices_raises_rather_than_returning_empty() -> None:
    with pytest.raises(LlmError):
        _to_completion({"choices": []}, DEFAULT_MODEL)


def test_an_error_body_raises() -> None:
    with pytest.raises(LlmError):
        _to_completion({"error": {"message": "rate limited"}}, DEFAULT_MODEL)


# -- configuration ----------------------------------------------------------

def test_a_missing_key_fails_fast_with_an_actionable_message(monkeypatch) -> None:
    # Same reasoning as the equivalent Anthropic test: the module-relative
    # .env lookup is not isolated by a chdir, so the lookup itself is patched.
    monkeypatch.setattr("concordance.llm.groq._key_from_environment", lambda: None)
    with pytest.raises(LlmError, match="GROQ_API_KEY"):
        GroqProvider(api_key=None)


def test_the_api_key_never_appears_in_a_raised_error() -> None:
    import io
    import urllib.error

    from concordance.llm.groq import _describe, _redact

    secret = "secret-value-123"
    body = json.dumps({"error": {"message": f"bad key: {secret}"}}).encode()
    error = urllib.error.HTTPError(
        url="https://api.groq.com/openai/v1/chat/completions",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(body),
    )
    assert secret not in _redact(_describe(error), secret)

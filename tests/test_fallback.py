"""Falling through to the next provider, and refusing to when that would hide a bug.

Written because of something that actually happened twice while building this:
Gemini's free tier is a daily, account-level quota, and exhausting it takes the
chat offline until the next day. A demo is the wrong place to find that out a
third time.

The tests that matter most are the negative ones. A chain that retries on every
error would turn one clear message ("your request body is malformed") into a
pile of confusing ones, and would let a defect in this project's own payload
masquerade as three separate outages. Falling through is reserved for failures
about reach.
"""

from __future__ import annotations

import pytest

from concordance.llm.base import Completion, LlmError, LlmUnreachable, Message
from concordance.llm.fallback import AllProvidersFailed, FallbackProvider


class Stub:
    """A provider that either answers or raises whatever it was handed."""

    def __init__(self, name: str, raises: Exception | None = None) -> None:
        self.name = name
        self._raises = raises
        self.calls = 0

    def complete(self, messages, *, system=None, tools=None, temperature=0.0):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return Completion(text=f"answered by {self.name}", model=self.name)


def ask(provider) -> Completion:
    return provider.complete([Message(role="user", text="hi")])


# -- when falling through is right --------------------------------------------

@pytest.mark.parametrize("status", [401, 402, 403, 408, 429, 500, 503])
def test_an_unavailable_provider_hands_over(status: int) -> None:
    """Quota, rejected key, and outages are all reasons another provider helps."""
    first = Stub("first", LlmError(f"HTTP {status}", status=status))
    second = Stub("second")

    result = ask(FallbackProvider([first, second]))

    assert result.text == "answered by second"
    assert first.calls == 1 and second.calls == 1


def test_an_unreachable_host_hands_over() -> None:
    """The case with no status at all: DNS failure, blocked egress, no network."""
    first = Stub("first", LlmUnreachable("Could not reach the host"))
    second = Stub("second")

    assert ask(FallbackProvider([first, second])).text == "answered by second"


def test_it_keeps_going_past_several_dead_providers() -> None:
    chain = [
        Stub("a", LlmError("quota", status=429)),
        Stub("b", LlmUnreachable("no route")),
        Stub("c"),
    ]
    assert ask(FallbackProvider(chain)).text == "answered by c"
    assert [s.calls for s in chain] == [1, 1, 1]


def test_the_provider_that_answered_is_recorded() -> None:
    """An answer from a different model than the one selected must not be silent."""
    provider = FallbackProvider([Stub("first", LlmError("q", status=429)), Stub("second")])
    ask(provider)
    assert provider.active.name == "second"
    assert provider.name == "second"


# -- when falling through would hide a real bug -------------------------------

def test_a_malformed_request_is_raised_immediately() -> None:
    """HTTP 400 is this project's own payload being wrong.

    Every provider would reject it identically, so trying the next one only
    buries the message that explains the actual defect.
    """
    first = Stub("first", LlmError("bad request: unknown field", status=400))
    second = Stub("second")

    with pytest.raises(LlmError, match="unknown field"):
        ask(FallbackProvider([first, second]))

    assert second.calls == 0, "the next provider must not be tried"


def test_a_failure_with_no_status_is_raised_immediately() -> None:
    """A malformed *response*, or a blocked prompt, is not an availability problem."""
    first = Stub("first", LlmError("returned a malformed response"))
    second = Stub("second")

    with pytest.raises(LlmError, match="malformed response"):
        ask(FallbackProvider([first, second]))
    assert second.calls == 0


def test_the_first_provider_is_used_when_it_works() -> None:
    first = Stub("first")
    second = Stub("second")
    assert ask(FallbackProvider([first, second])).text == "answered by first"
    assert second.calls == 0


# -- exhaustion and configuration ---------------------------------------------

def test_exhausting_the_chain_names_every_attempt() -> None:
    """A reader has to be able to see which providers were tried and why each failed."""
    chain = [
        Stub("gemini:flash", LlmError("quota exhausted", status=429)),
        Stub("groq:llama", LlmUnreachable("blocked by egress policy")),
    ]
    with pytest.raises(AllProvidersFailed) as caught:
        ask(FallbackProvider(chain))

    message = str(caught.value)
    assert "gemini:flash" in message and "quota exhausted" in message
    assert "groq:llama" in message and "blocked by egress policy" in message


def test_an_empty_chain_says_which_keys_would_fix_it() -> None:
    with pytest.raises(LlmError) as caught:
        FallbackProvider([])
    for name in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY"):
        assert name in str(caught.value)


# -- which failures count, decided by status not by prose ---------------------

@pytest.mark.parametrize(
    "status,expected",
    [(400, False), (401, True), (404, False), (429, True), (500, True), (None, False)],
)
def test_availability_is_judged_by_status_code(status, expected: bool) -> None:
    assert LlmError("x", status=status).is_provider_unavailable is expected


def test_unreachable_counts_even_without_a_status() -> None:
    assert LlmUnreachable("no route").is_provider_unavailable is True


# -- building the chain from what is actually configured ----------------------

def test_the_preferred_provider_comes_first(monkeypatch) -> None:
    from concordance.llm import fallback

    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("GROQ_API_KEY", "q")
    monkeypatch.setenv("OPENROUTER_API_KEY", "r")

    providers, skipped = fallback.available_providers(preferred="groq")

    assert providers[0].name.startswith("groq")
    assert {p.name.split(":")[0] for p in providers} == {
        "gemini",
        "anthropic",
        "groq",
        "openrouter",
    }
    assert skipped == []


def test_a_provider_without_a_key_is_reported_not_silently_dropped(monkeypatch) -> None:
    """A one-provider chain looks exactly like a working one until it matters."""
    from concordance.llm import fallback

    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setattr("concordance.llm.anthropic._key_from_environment", lambda: None)
    monkeypatch.setattr("concordance.llm.groq._key_from_environment", lambda: None)

    providers, skipped = fallback.available_providers(preferred="gemini")

    assert [p.name.split(":")[0] for p in providers] == ["gemini"]
    assert any("anthropic" in note for note in skipped)
    assert any("groq" in note for note in skipped)


def test_a_model_name_is_only_given_to_the_provider_it_belongs_to(monkeypatch) -> None:
    """Model ids are provider-specific; handing Gemini's to Groq breaks the chain."""
    from concordance.llm import fallback

    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GROQ_API_KEY", "q")
    monkeypatch.setattr("concordance.llm.anthropic._key_from_environment", lambda: None)

    providers, _ = fallback.available_providers(
        preferred="gemini", model="gemini-3.6-flash"
    )
    by_name = {p.name.split(":")[0]: p for p in providers}

    assert by_name["gemini"].model == "gemini-3.6-flash"
    assert by_name["groq"].model != "gemini-3.6-flash"


# -- a billing failure wearing a client-error status --------------------------
# Found by running the chain for real, not by reasoning about it: Anthropic
# answers an exhausted credit balance with 400 rather than 402, which the
# status-only rule classified as this project's own bug and refused to fall
# through on.

def test_anthropics_real_billing_400_hands_over() -> None:
    exact = (
        "Anthropic returned HTTP 400: Your credit balance is too low to access "
        "the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."
    )
    first = Stub("anthropic", LlmError(exact, status=400))
    second = Stub("groq")

    assert ask(FallbackProvider([first, second])).text == "answered by groq"


@pytest.mark.parametrize(
    "message",
    [
        "Your credit balance is too low to access the Anthropic API.",
        "You exceeded your current quota, please check your plan and billing details.",
        "insufficient_quota",
    ],
)
def test_billing_wordings_count_as_unavailable(message: str) -> None:
    assert LlmError(message, status=400).is_provider_unavailable is True


@pytest.mark.parametrize(
    "message",
    [
        "unknown field 'tolls' in request body",
        "messages: final assistant content cannot end with trailing whitespace",
        "max_tokens is required",
    ],
)
def test_an_ordinary_malformed_400_still_stops_the_chain(message: str) -> None:
    """The narrow billing match must not swallow real payload bugs."""
    assert LlmError(message, status=400).is_provider_unavailable is False

    first = Stub("first", LlmError(message, status=400))
    second = Stub("second")
    with pytest.raises(LlmError):
        ask(FallbackProvider([first, second]))
    assert second.calls == 0

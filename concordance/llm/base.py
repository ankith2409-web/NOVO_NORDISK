"""The provider contract for language models.

The model sits behind this interface for two reasons. Swapping providers stays a
configuration change rather than a rewrite -- the project was designed against
Claude and now runs on Gemini, which is exactly the churn an interface is meant
to absorb. And tests get a deterministic fake instead of a network call, so the
agent loop can be verified without a key, a quota, or a flaky connection.

Nothing here decides *what* a requirement says. Requirement content is derived
deterministically from the graph; a provider only ever rephrases it or drives a
conversation over tools that read the graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolSpec:
    """A function the model may call, described in JSON Schema."""

    name: str
    description: str
    #: JSON Schema object describing the arguments.
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    #: Provider-assigned id, echoed back with the result where supported.
    call_id: str | None = None
    #: Opaque provider state that must be replayed verbatim when this call is
    #: sent back as history. Gemini 3 rejects a conversation whose function
    #: calls have lost their thought signature, so the value has to survive the
    #: round trip even though nothing here interprets it.
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Message:
    """One turn. ``role`` is 'user', 'model' or 'tool'."""

    role: str
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    #: For a tool result: which call it answers, and what it returned.
    tool_name: str | None = None
    tool_result: Any = None
    #: The originating call's id, carried alongside ``tool_name``. Gemini pairs
    #: a result to its call by function name and never reads this; Anthropic
    #: pairs strictly by id, so a provider that drops it here cannot round-trip
    #: a conversation without the API rejecting a tool_result that references
    #: an id no earlier tool_use block declared.
    tool_call_id: str | None = None


@dataclass(frozen=True)
class Completion:
    text: str
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    #: Provider token accounting, when reported.
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


#: Wordings observed from real providers that report a billing or quota problem
#: with a client-error status instead of 402 or 429. Kept deliberately short and
#: specific: every phrase here is one an actual API returned, not a guess at what
#: one might say, because a loose match would start swallowing genuine
#: malformed-request errors.
_BILLING_PHRASES = (
    "credit balance is too low",
    "quota",
    "billing",
    "insufficient_quota",
)


class LlmError(RuntimeError):
    """A provider call failed in a way the caller should surface, not retry blindly.

    ``status`` carries the HTTP code when the failure came from a provider's
    API, which is what lets a fallback chain decide whether trying a different
    provider could possibly help. Reading that decision out of the message text
    instead would mean matching on prose that each provider words differently.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status

    @property
    def is_provider_unavailable(self) -> bool:
        """Would a different provider plausibly have answered this?

        Only for failures about *reach*: an exhausted quota, a key that is not
        accepted, the service being down, or the host being unreachable.

        Deliberately false for a plain 400. A rejected request body is this
        project's own bug -- every provider would reject it equally -- and
        rolling on to the next one would turn a clear, fixable error into a
        confusing chain of them, which is exactly how a real defect gets
        mistaken for someone else's outage.

        The exception is real and was found by running this rather than
        reasoning about it: Anthropic answers an exhausted credit balance with
        400, not 402, saying "Your credit balance is too low to access the
        Anthropic API." That is unambiguously an availability problem wearing a
        client-error status, so the few words that identify it are matched
        narrowly -- narrowly enough that an ordinary malformed-payload 400 is
        still raised immediately.
        """
        if self.status is None:
            return False
        if self.status in (401, 402, 403, 408, 429) or self.status >= 500:
            return True
        if self.status == 400:
            return any(phrase in self.message.lower() for phrase in _BILLING_PHRASES)
        return False


class LlmUnreachable(LlmError):
    """The provider's host could not be reached at all."""

    @property
    def is_provider_unavailable(self) -> bool:
        return True


@runtime_checkable
class LlmProvider(Protocol):
    """What the agent needs from a language model."""

    name: str

    def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        """Continue the conversation, optionally calling tools."""
        ...

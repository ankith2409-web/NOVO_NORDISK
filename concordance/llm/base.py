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


class LlmError(RuntimeError):
    """A provider call failed in a way the caller should surface, not retry blindly."""


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

"""The provider you get when there is no provider.

The server used to refuse to start without a language-model key. That is the
right rule for a chatbot and the wrong rule for this program: the chat is one
collapsible panel beside eight pages -- the dashboard, the SQL, the documents,
the model browser, drift, the warehouse check, the confirmation queue -- and
not one of them asks a model anything. Every figure this tool prints is
computed by running SQL against the file's own rows, on purpose, so that it can
be checked rather than trusted.

So somebody who cloned the repository to look at what it does got an error
message and no program at all, over a feature they had not asked to use. That
is a worse failure than the one it was guarding against.

This stands in instead. Every page works; the one panel that genuinely needs a
key says so, in the words the provider builders themselves used, when somebody
tries to use it. `LlmError` is already what the chat route turns into a reply,
so nothing else has to know this exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from concordance.llm.base import Completion, LlmError, Message, ToolSpec


@dataclass
class UnconfiguredProvider:
    """Answers every question with what to set to get a real answer."""

    #: Why each provider could not be built, in the order they were tried.
    reasons: list[str] = field(default_factory=list)
    name: str = "unconfigured"

    @property
    def explanation(self) -> str:
        head = (
            "This server was started without a language model, so the chat "
            "cannot answer. Everything else on this page was read from the "
            "file itself and does not need one."
        )
        if not self.reasons:
            return head
        return head + " Providers tried:\n" + "\n".join(f"  {r}" for r in self.reasons)

    def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        raise LlmError(self.explanation)

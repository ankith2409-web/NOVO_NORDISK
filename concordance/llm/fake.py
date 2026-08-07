"""A scripted provider for tests.

The agent loop has behaviour worth testing that has nothing to do with any
particular model: does it reject a tool that was never declared, does it stop
when the model stops asking for tools, does it give up cleanly rather than loop
forever. Those need a provider that does exactly what the test says, on every
run, with no network and no key.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from concordance.llm.base import Completion, Message, ToolCall, ToolSpec


@dataclass
class FakeProvider:
    """Replays a fixed script of completions, recording what it was asked."""

    script: list[Completion]
    name: str = "fake"
    #: Every call the loop made, for assertions about prompts and tools.
    calls: list[dict] = field(default_factory=list)

    def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        self.calls.append(
            {
                "messages": list(messages),
                "system": system,
                "tools": [t.name for t in tools] if tools else None,
                "temperature": temperature,
            }
        )
        if not self.script:
            return Completion(text="(script exhausted)", model=self.name)
        return self.script.pop(0)


def says(text: str) -> Completion:
    """A plain textual reply, ending the exchange."""
    return Completion(text=text, model="fake")


def calls_tool(tool: str, arguments: dict | None = None) -> Completion:
    """A completion that asks for one tool call.

    Arguments are passed as an explicit dict rather than keywords: several tools
    take a parameter called ``name``, which would collide with a keyword-based
    signature.
    """
    return Completion(
        text="",
        tool_calls=(ToolCall(name=tool, arguments=arguments or {}),),
        model="fake",
    )

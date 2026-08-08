"""The conversation loop over a semantic model.

The agent answers questions by calling tools that read the graph, so what it
says is grounded in the extracted model rather than recalled or guessed. It
holds no authority over content: it selects tools, and phrases what they return.

Every tool call is validated against the registry before it runs. That is not
defensive habit -- Gemini demonstrably invents plausible tool names when the
declared surface does not fit the question, calling a ``list_measures`` that was
never offered. An invented call is answered with an error the model can recover
from on its next turn, rather than crashing the loop or being silently ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from concordance.agent.tools import ModelTools
from concordance.graph.csg import SemanticGraph
from concordance.llm.base import Completion, LlmProvider, Message, ToolCall

SYSTEM_PROMPT = """\
You are a documentation assistant for a Power BI semantic model. You help \
analysts and engineers understand what a model contains: its tables, joins, \
measures, DAX logic, hierarchies and KPIs.

Rules you must follow:

1. Answer only from what the tools return. You have not seen this model before \
and you cannot know its contents any other way. If the tools do not contain \
the answer, say so plainly.
2. Never invent a measure, table, column or DAX expression. If a name is not \
found, say it is not in the model and offer the closest matches the tool \
returned.
3. Quote DAX exactly as the tool returns it. Do not tidy, reformat or \
"correct" an expression.
4. When a question is about the effect of a change, use what_uses -- \
dependencies are recorded in the model and must not be guessed at.
5. Be concise and concrete. Prefer naming the specific objects involved over \
describing them in general terms.

If a tool reports that something could not be extracted, pass that on rather \
than filling the gap yourself.
"""

#: Enough turns for search -> describe -> follow-up, without allowing a loop to
#: run away if the model keeps calling tools.
MAX_TOOL_ROUNDS = 6

#: How many past question-and-answer exchanges to carry forward. History is
#: resent in full on every request, and each exchange can include sizeable tool
#: results, so an unbounded conversation costs steadily more tokens per question
#: and eventually will not fit at all. Twelve is far more context than a demo
#: conversation needs while keeping the payload flat.
MAX_HISTORY_EXCHANGES = 12


@dataclass
class Exchange:
    """One question and everything that went into answering it."""

    question: str
    answer: str
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    rejected_calls: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""

    @property
    def grounded(self) -> bool:
        """Did the answer rest on the model, or only on the language model?"""
        return bool(self.tool_calls)


class ModelChat:
    """A conversation about one semantic model."""

    def __init__(
        self,
        graph: SemanticGraph,
        provider: LlmProvider,
        max_rounds: int = MAX_TOOL_ROUNDS,
        max_history: int = MAX_HISTORY_EXCHANGES,
    ) -> None:
        self.tools = ModelTools(graph)
        self.provider = provider
        self.max_rounds = max_rounds
        self.max_history = max_history
        self.history: list[Message] = []

    def ask(self, question: str) -> Exchange:
        """Answer one question, running whatever tool calls it needs."""
        exchange = Exchange(question=question, answer="")
        self._trim_history()
        self.history.append(Message(role="user", text=question))

        specs = self.tools.specs()
        known = {spec.name for spec in specs}

        for _ in range(self.max_rounds):
            completion: Completion = self.provider.complete(
                self.history,
                system=self._system_prompt(),
                tools=specs,
                temperature=0.0,
            )
            _accumulate(exchange, completion)

            if not completion.wants_tools:
                exchange.answer = completion.text
                self.history.append(Message(role="model", text=completion.text))
                return exchange

            self.history.append(
                Message(role="model", text=completion.text, tool_calls=completion.tool_calls)
            )

            for call in completion.tool_calls:
                if call.name not in known:
                    exchange.rejected_calls.append(call.name)
                else:
                    exchange.tool_calls.append((call.name, call.arguments))
                self.history.append(self._run(call))

        # Out of rounds: ask once more, without tools, so the user gets an
        # answer rather than silence.
        final = self.provider.complete(
            self.history,
            system=self._system_prompt(),
            tools=None,
            temperature=0.0,
        )
        _accumulate(exchange, final)
        exchange.answer = final.text or (
            "I could not settle this within the allowed number of tool calls."
        )
        self.history.append(Message(role="model", text=exchange.answer))
        return exchange

    # -- internals ---------------------------------------------------------

    def _trim_history(self) -> None:
        """Drop the oldest exchanges once the conversation grows past the limit.

        Trimming happens only at a user turn, never mid-exchange. A model turn
        carrying a ``functionCall`` and the tool turn answering it must travel
        together -- Gemini rejects a conversation where one appears without the
        other, so cutting at an arbitrary index would break the next request
        rather than merely shortening it.
        """
        if self.max_history <= 0:
            return

        starts = [i for i, message in enumerate(self.history) if message.role == "user"]
        if len(starts) <= self.max_history:
            return

        cut = starts[len(starts) - self.max_history]
        self.history = self.history[cut:]

    def _run(self, call: ToolCall) -> Message:
        """Execute a tool call. Validation lives in the dispatcher."""
        result = self.tools.dispatch(call.name, call.arguments)
        return Message(
            role="tool",
            tool_name=call.name,
            tool_result=result,
            # Threaded through so a provider that pairs by id -- Anthropic does,
            # Gemini does not -- can round-trip the conversation correctly.
            tool_call_id=call.call_id,
        )

    def _system_prompt(self) -> str:
        model = self.tools.model
        return (
            f"{SYSTEM_PROMPT}\n"
            f"The model you are describing is called {model.name!r} and was read "
            f"from a {model.source_type} source."
        )


def _accumulate(exchange: Exchange, completion: Completion) -> None:
    exchange.model = completion.model or exchange.model
    for key, value in completion.usage.items():
        exchange.usage[key] = exchange.usage.get(key, 0) + value

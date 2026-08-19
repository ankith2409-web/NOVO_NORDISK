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
measures, DAX logic, hierarchies, KPIs, security roles, and where its data is \
loaded from.

## Grounding rules

These are not style preferences. The whole value of this assistant is that a \
reader can trust what it says about a model nobody has documented.

1. Answer only from what the tools return. You have not seen this model before \
and you cannot know its contents any other way. If the tools do not contain \
the answer, say so plainly.
2. Never invent a measure, table, column, role or DAX expression. If a name is \
not found, say it is not in the model and offer the closest matches the tool \
returned.
3. Quote DAX and M exactly as the tool returns it. Do not tidy, reformat or \
"correct" an expression.
4. When a question is about the effect of a change, use what_uses -- \
dependencies are recorded in the model and must not be guessed at.
5. Do not assert business meaning the model does not record. You may explain \
what an expression *computes*, because that is readable from the expression \
itself. You may not say why the business wants it, what a threshold signifies, \
or what a metric is "for", unless a description field actually says so. When \
you are describing intent rather than mechanics, say which one you are doing.
6. If a tool reports that something could not be extracted, pass that on \
rather than filling the gap yourself.

## Not every message is a question about the model

When someone greets you, thanks you, or asks what you can do, answer them the \
way a person would: briefly, with no tool calls, and offer two or three \
concrete things worth asking about this particular model. Someone who typed \
"hi" has not asked for statistics, and answering them with a table count is a \
non-sequitur that buries the useful reply.

Use a tool when a question actually needs a fact from the model. One \
well-chosen call beats three speculative ones.

If a question is about Power BI or DAX in general rather than about this \
model, you may answer it from your own knowledge -- but say that is what you \
are doing, so it is not mistaken for something read out of the model.

## Style

Be concise and concrete. Name the specific objects involved rather than \
describing them in general terms. Use backticks for object names and DAX. \
Reach for a short list only where there genuinely is a list; prefer two \
accurate sentences over a padded paragraph.
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

#: Messages that are not questions about the model, matched whole after
#: normalisation. Kept as exact sets rather than patterns on purpose: "hi"
#: is a greeting, but "hi, what measures are there?" is a question, and a
#: substring rule would answer the second with a wave. Missing an unusual
#: greeting costs one ordinary LLM round; swallowing a real question costs
#: the answer.
_GREETINGS = frozenset(
    {
        "hi", "hii", "hiii", "hey", "heya", "hiya", "hello", "helo", "yo",
        "howdy", "hi there", "hello there", "hey there", "good morning",
        "good afternoon", "good evening", "greetings", "sup", "whats up",
        "what's up", "hi again", "hello again",
    }
)

_THANKS = frozenset(
    {
        "thanks", "thank you", "thanks a lot", "thankyou", "ty", "thx",
        "cheers", "nice", "great", "cool", "awesome", "perfect", "got it",
        "ok", "okay", "k", "understood", "makes sense", "thanks!",
    }
)

_FAREWELLS = frozenset(
    {"bye", "goodbye", "see you", "see ya", "cya", "later", "good night"}
)

#: Answered from the tool registry rather than by a provider, so the list of
#: what this assistant can do cannot drift from what it actually offers.
_CAPABILITY_QUESTIONS = frozenset(
    {
        "help", "what can you do", "what can you do?", "what do you do",
        "who are you", "what are you", "how can you help",
        "how can you help me", "what can i ask", "what can i ask you",
        "what should i ask", "capabilities", "commands", "options",
    }
)


@dataclass
class Exchange:
    """One question and everything that went into answering it."""

    question: str
    answer: str
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    rejected_calls: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    #: True when this was a greeting or a "what can you do", answered here
    #: without a language model. It makes no claim about the model's contents,
    #: so the interface must not mark it as an ungrounded assertion -- that
    #: warning means "this said something about your data that nothing
    #: verified", which is precisely what a hello does not do.
    conversational: bool = False

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
        opener = self._conversational_reply(question)
        if opener is not None:
            # Answered without reaching a provider at all. The system prompt
            # asks for this too, but asking is not the same as ensuring: the
            # providers in the fallback chain include small free-tier models
            # whose instruction-following is unreliable, and the failure this
            # prevents -- "hi" answered with a recitation of table counts --
            # was reported from a real deployment. Handling it here makes the
            # behaviour identical on every provider, costs no tokens, and can
            # be tested without a network.
            #
            # Deliberately not added to `self.history`: a greeting carries no
            # information the next question needs, and keeping it would spend
            # context on every later request for nothing.
            return Exchange(question=question, answer=opener, conversational=True)

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

    # -- conversational openers --------------------------------------------

    def _conversational_reply(self, question: str) -> str | None:
        """A reply for a message that is not a question about the model.

        Matched against the *whole* message, normalised, and nothing else.
        "hi" is a greeting; "hi, which measures are there?" is a question and
        must fall through to the model untouched. Matching a substring would
        swallow real questions, which is a far worse failure than missing a
        hello.
        """
        text = question.strip().casefold().strip(" .!?,…")
        text = " ".join(text.split())
        if not text:
            return None

        if text in _GREETINGS:
            return f"Hello. {self._what_to_ask()}"
        if text in _THANKS:
            return "You're welcome."
        if text in _FAREWELLS:
            return "Goodbye."
        if text in _CAPABILITY_QUESTIONS:
            return self._what_i_do()
        return None

    def _what_i_do(self) -> str:
        model = self.tools.model
        return (
            f"I answer questions about the semantic model `{model.name}` by reading "
            f"what was extracted from it -- never from memory, and never by guessing. "
            f"I can show you a measure's DAX and what it depends on, trace what would "
            f"break if you changed something, explain where a table's data is loaded "
            f"from and what happens to it on the way in, search inside expressions "
            f"rather than just names, report row-level security, and flag structural "
            f"things a reviewer would want to see.\n\n{self._what_to_ask()}"
        )

    def _what_to_ask(self) -> str:
        """Suggestions naming objects this model actually contains.

        Generated rather than fixed, because a canned suggestion that names a
        measure the open model does not have teaches someone the assistant
        invents things -- in the first sentence they ever read from it.
        """
        model = self.tools.model
        suggestions: list[str] = []

        measures = sorted(model.measures, key=lambda m: m.name)
        if measures:
            suggestions.append(f"What does `{measures[0].name}` actually compute?")
        if len(measures) > 1:
            suggestions.append(f"What would break if I changed `{measures[-1].name}`?")

        loaded = next((t for t in model.tables if t.power_query), None)
        if loaded is not None:
            suggestions.append(f"Where does `{loaded.name}` load its data from?")
        if model.roles:
            suggestions.append("Which roles restrict what, and with what filter?")
        if any(not r.is_active for r in model.relationships):
            suggestions.append("Which joins are inactive, and does anything use them?")
        if not suggestions:
            suggestions.append("What is in this model?")

        listed = "\n".join(f"- {line}" for line in suggestions[:3])
        return f"You could ask:\n{listed}"


def _accumulate(exchange: Exchange, completion: Completion) -> None:
    exchange.model = completion.model or exchange.model
    for key, value in completion.usage.items():
        exchange.usage[key] = exchange.usage.get(key, 0) + value

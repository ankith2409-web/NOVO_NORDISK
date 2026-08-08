"""Trying the next provider when one becomes unavailable.

This exists because of something that happened repeatedly while building this
project, not something imagined: Gemini's free tier is a daily, account-level
quota, and exhausting it takes the chat offline until the next day -- a new key
on the same account does not help. During development that happened twice, and
a demo is exactly the wrong moment to discover it a third time.

What it does *not* do is retry indiscriminately. Falling through on every error
would turn one clear, fixable message into a chain of confusing ones and hide
this project's own bugs behind an apparent outage. Only failures about *reach*
move to the next provider -- an exhausted quota, a rejected key, the service
being down, an unreachable host. A rejected request body (HTTP 400) is a defect
in what this code sent, identical everywhere, and is raised immediately. That
distinction lives on ``LlmError.is_provider_unavailable`` so it is decided by a
status code rather than by matching prose.
"""

from __future__ import annotations

from concordance.llm.base import Completion, LlmError, LlmProvider, Message, ToolSpec


class AllProvidersFailed(LlmError):
    """Every provider in the chain was tried and none could answer."""


class FallbackProvider:
    """Asks each provider in turn until one answers.

    Order is the caller's preference, best first. The chain is fixed at
    construction: a provider whose key is missing never enters it, so a missing
    key is reported once at startup rather than rediscovered on every question.
    """

    def __init__(self, providers: list[LlmProvider]) -> None:
        if not providers:
            raise LlmError(
                "No language model provider is configured. Set GEMINI_API_KEY, "
                "ANTHROPIC_API_KEY or GROQ_API_KEY in the environment or in a "
                ".env file at the project root."
            )
        self.providers = providers
        #: Which provider answered last. The interface shows this, because an
        #: answer that silently came from a different model than the one the
        #: operator selected is exactly the kind of thing a reader should not
        #: have to guess at.
        self.active = providers[0]
        self.name = providers[0].name

    def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        attempts: list[str] = []

        for provider in self.providers:
            try:
                completion = provider.complete(
                    messages, system=system, tools=tools, temperature=temperature
                )
            except LlmError as error:
                attempts.append(f"{provider.name}: {error}")
                if error.is_provider_unavailable:
                    continue
                # A failure this provider owns -- a malformed request, a blocked
                # prompt. Another provider would fail the same way, and burying
                # the real message under further attempts helps nobody.
                raise

            self.active = provider
            self.name = provider.name
            return completion

        raise AllProvidersFailed(
            "Every configured provider was unavailable. Tried:\n  "
            + "\n  ".join(attempts)
        )


def available_providers(
    preferred: str = "gemini",
    model: str | None = None,
    base_url: str | None = None,
) -> tuple[list[LlmProvider], list[str]]:
    """Build every provider whose key is present, preferred one first.

    Returns the providers alongside a note for each one that could not be
    built, so a caller can tell an operator *why* their chain is shorter than
    they expected -- a silently one-provider chain looks identical to a working
    one right up until the moment it matters.
    """
    from concordance.llm.anthropic import DEFAULT_MODEL as ANTHROPIC_MODEL
    from concordance.llm.anthropic import AnthropicProvider
    from concordance.llm.gemini import DEFAULT_MODEL as GEMINI_MODEL
    from concordance.llm.gemini import GeminiProvider
    from concordance.llm.groq import DEFAULT_MODEL as GROQ_MODEL
    from concordance.llm.groq import GroqProvider

    # Only the explicitly preferred provider takes a caller-supplied model name:
    # a model identifier is provider-specific, and handing Gemini's to Anthropic
    # would fail on every question in the chain for a reason nobody could read.
    builders = {
        "gemini": lambda mine: GeminiProvider(model=mine or GEMINI_MODEL),
        "anthropic": lambda mine: AnthropicProvider(
            model=mine or ANTHROPIC_MODEL, base_url=base_url
        ),
        "groq": lambda mine: GroqProvider(model=mine or GROQ_MODEL, base_url=base_url),
    }

    order = [preferred] + [name for name in builders if name != preferred]
    providers: list[LlmProvider] = []
    skipped: list[str] = []

    for name in order:
        try:
            providers.append(builders[name](model if name == preferred else None))
        except LlmError as error:
            skipped.append(f"{name} ({error})")

    return providers, skipped

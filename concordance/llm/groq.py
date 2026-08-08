"""Groq provider, over its OpenAI-compatible chat completions API.

Added as a genuinely free path: Groq's free tier needs no card, and its rate
limits (per-model requests and tokens per minute) are generous enough for a
documentation assistant answering a handful of questions in a demo. Gemini's
free tier is also free, but its quota is daily and account-level -- once
exhausted, a new key on the same account does not help, only time or a
different account does. Groq is the alternative that does not depend on either.

Uses ``urllib`` rather than an SDK, matching the other providers: the surface
needed is one POST with a JSON body, and avoiding a dependency keeps the
install trivial and the behaviour inspectable.

Temperature defaults to 0 for the same reason it does everywhere else in this
project -- draft EU GMP Annex 22 expects AI used in GMP-critical work to behave
deterministically, and a documentation generator has no use for creative
variation: the same model should yield the same requirement text twice.

Written against Groq's publicly documented OpenAI-compatible API and not
exercised against a live key from this environment: this sandbox's egress
policy blocks ``api.groq.com`` outright (confirmed via a direct CONNECT, which
the proxy answered with a policy 403), the same as the Claude-compatible
gateway this project's Anthropic provider was written for. The fix is the same
in both cases -- verify with one real, free question from a machine that can
reach the host, not route around a policy denial from here.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from concordance.llm.base import Completion, LlmError, Message, ToolCall, ToolSpec

_DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"

#: A model with tool-calling support on the free tier. Named explicitly rather
#: than "whatever is current" because a documentation assistant depends on tool
#: use to be grounded at all -- silently landing on a model that cannot call
#: tools would make every answer look ungrounded without any error to explain
#: why.
DEFAULT_MODEL = "llama-3.3-70b-versatile"

#: Groq's free tier caps tokens per minute per model as well as requests per
#: minute. Bounding a single response keeps one long-winded answer from eating
#: a meaningful fraction of that per-minute budget on its own -- generous
#: enough for a paragraph of documentation prose, not so large that a single
#: reply can exhaust the limit.
DEFAULT_MAX_TOKENS = 1024


class GroqProvider:
    """Calls Groq's chat completions endpoint, OpenAI-compatible."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        timeout: int = 60,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.name = f"groq:{model}"
        self._api_key = api_key or _key_from_environment()
        self._base_url = (
            base_url or os.environ.get("GROQ_BASE_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        if not self._api_key:
            raise LlmError(
                "No Groq API key. Set GROQ_API_KEY in the environment or in a "
                ".env file at the project root. Keys are free at console.groq.com, "
                "no card required."
            )

    # -- provider contract -------------------------------------------------

    def complete(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.0,
    ) -> Completion:
        chat_messages = _to_messages(messages)
        if system:
            # OpenAI-style chat completions carry the system prompt as an
            # ordinary message in the list, not a separate top-level field the
            # way Anthropic's API takes it.
            chat_messages.insert(0, {"role": "system", "content": system})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": chat_messages,
            "temperature": temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        data = self._post(payload)
        return _to_completion(data, self.model)

    # -- transport -----------------------------------------------------------

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = _describe(error)
            raise LlmError(
                f"Groq returned HTTP {error.code}: {_redact(detail, self._api_key)}"
            ) from None
        except urllib.error.URLError as error:
            raise LlmError(f"Could not reach {self._base_url}: {error.reason}") from None
        except json.JSONDecodeError as error:
            raise LlmError(f"Groq returned a malformed response: {error}") from None


def _key_from_environment() -> str | None:
    """Read the key from the environment, falling back to a local .env file."""
    value = os.environ.get("GROQ_API_KEY")
    if value:
        return value

    # A .env at the project root is the documented place to keep it, parsed
    # here rather than pulled in as a dependency -- matching the other providers.
    for candidate in (".env", os.path.join(os.path.dirname(__file__), "..", "..", ".env")):
        try:
            with open(candidate, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    if key.strip() == "GROQ_API_KEY":
                        return value.strip().strip("'\"")
        except OSError:
            continue
    return None


def _to_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Translate the provider-agnostic history into OpenAI-style chat messages.

    Two things this format wants that neither Gemini's nor Anthropic's does.
    Tool call arguments are a JSON-encoded *string*, not a nested object --
    unlike Anthropic's ``input`` and Gemini's ``args``, which are both objects
    the transport itself serialises. And a tool result is its own top-level
    role, paired to its call by id exactly the way Anthropic's is; the id
    plumbing this needs is the same ``tool_call_id`` added to ``Message`` for
    the Anthropic provider, not something reinvented here.
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id or message.tool_name or "",
                    "content": _as_text(message.tool_result),
                }
            )
            continue

        role = "assistant" if message.role == "model" else "user"
        entry: dict[str, Any] = {"role": role, "content": message.text or None}
        if message.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": call.call_id or call.name,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
                for call in message.tool_calls
            ]
        out.append(entry)
    return out


def _as_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, default=str)


def _to_completion(data: dict[str, Any], model: str) -> Completion:
    if "error" in data:
        raise LlmError(str(data["error"].get("message", data["error"]))[:400])

    choices = data.get("choices") or []
    if not choices:
        raise LlmError(f"Groq returned no choices: {json.dumps(data)[:400]}")

    message = choices[0].get("message", {}) or {}
    text = message.get("content") or ""

    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function", {}) or {}
        # Arguments arrive as a JSON string; a malformed one from the model
        # should not crash the loop, so it degrades to an empty call rather
        # than raising -- the dispatcher's own validation will reject a tool
        # call it cannot use for a clearer reason than a parse error would.
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        calls.append(
            ToolCall(
                name=function.get("name", ""),
                arguments=arguments,
                call_id=raw.get("id"),
            )
        )

    usage_raw = data.get("usage", {}) or {}
    usage = {
        k: v
        for k, v in {
            "input_tokens": usage_raw.get("prompt_tokens"),
            "output_tokens": usage_raw.get("completion_tokens"),
        }.items()
        if isinstance(v, int)
    }

    return Completion(
        text=text.strip(),
        tool_calls=tuple(calls),
        usage=usage,
        model=data.get("model", model),
    )


def _describe(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read())
        return str(body.get("error", {}).get("message", body))[:400]
    except Exception:
        return error.reason if isinstance(error.reason, str) else str(error.reason)


def _redact(text: str, secret: str | None) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text

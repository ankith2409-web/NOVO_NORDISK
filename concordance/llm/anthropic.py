"""Anthropic provider, over the REST Messages API.

This is where the project started -- Concordance was designed against Claude,
then moved to Gemini when a paid Anthropic key was not available. The interface
in ``llm/base.py`` exists precisely so that swap-back is a configuration change,
not a rewrite: nothing about the graph, the requirements, or the agent loop
knows which model answers a question.

Uses ``urllib`` rather than the ``anthropic`` SDK, matching ``gemini.py``: the
surface needed is one POST with a JSON body, and avoiding a dependency keeps the
install trivial and the behaviour inspectable.

Temperature defaults to 0 for the same reason it does in the Gemini provider --
draft EU GMP Annex 22 expects AI used in GMP-critical work to behave
deterministically, and a documentation generator has no use for creative
variation: the same model should yield the same requirement text twice.

The base URL is configurable because a Claude-compatible gateway is a different
host from Anthropic's own API but speaks the identical request and response
shape -- same headers, same ``/v1/messages`` path, same tool-use block format.
This has been written against Anthropic's publicly documented API and has not
been exercised against a live key or a specific gateway from this environment:
the sandbox's network policy blocks the gateway host this project was pointed
at, and the fix is to verify locally, not to route around a policy denial.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from concordance.llm.base import (
    Completion,
    LlmError,
    LlmUnreachable,
    Message,
    ToolCall,
    ToolSpec,
)

_DEFAULT_BASE_URL = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"

#: Anthropic requires max_tokens; unlike Gemini there is no server-side default.
#: This is generous for one turn of documentation prose or a handful of tool
#: calls, without being large enough to let a single response run away with a
#: meaningful fraction of a small credit balance.
DEFAULT_MAX_TOKENS = 1024

#: Small and fast. Chosen as the default because this project's Gemini side
#: defaults to Flash for the same reason: a documentation assistant answering
#: from tool results does not need the largest model, and cost matters when the
#: key behind it may carry a limited balance.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class AnthropicProvider:
    """Calls Anthropic's Messages API, or a gateway that mirrors its shape."""

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
        self.name = f"anthropic:{model}"
        self._api_key = api_key or _key_from_environment()
        self._base_url = (
            base_url or os.environ.get("ANTHROPIC_BASE_URL") or _DEFAULT_BASE_URL
        ).rstrip("/")
        if not self._api_key:
            raise LlmError(
                "No Anthropic API key. Set ANTHROPIC_API_KEY in the environment or in "
                "a .env file at the project root."
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
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": temperature,
            "messages": _to_messages(messages),
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]

        data = self._post(payload)
        return _to_completion(data, self.model)

    # -- transport -----------------------------------------------------------

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._base_url}/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": _API_VERSION,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = _describe(error)
            # The key is sent as a header, not embedded in the URL, but an error
            # body could still echo request content back; redact defensively.
            raise LlmError(
                f"Anthropic returned HTTP {error.code}: {_redact(detail, self._api_key)}",
                status=error.code,
            ) from None
        except urllib.error.URLError as error:
            raise LlmUnreachable(f"Could not reach {self._base_url}: {error.reason}") from None
        except json.JSONDecodeError as error:
            raise LlmError(f"Anthropic returned a malformed response: {error}") from None


def _key_from_environment() -> str | None:
    """Read the key from the environment, falling back to a local .env file."""
    value = os.environ.get("ANTHROPIC_API_KEY")
    if value:
        return value

    # A .env at the project root is the documented place to keep it, parsed
    # here rather than pulled in as a dependency -- matching the Gemini provider.
    for candidate in (".env", os.path.join(os.path.dirname(__file__), "..", "..", ".env")):
        try:
            with open(candidate, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    if key.strip() == "ANTHROPIC_API_KEY":
                        return value.strip().strip("'\"")
        except OSError:
            continue
    return None


def _to_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Translate the provider-agnostic history into Anthropic's message shape.

    Anthropic represents a tool result as a ``user`` message containing a
    ``tool_result`` block, paired to its call strictly by id -- there is no
    separate "tool" role, and no fallback to matching by name the way Gemini's
    function-response part does. A tool_result whose ``tool_use_id`` does not
    exactly match an id declared by an earlier tool_use block in the same
    conversation is a request the API rejects outright, not a mismatch it
    tolerates.

    The id used on both sides has to be identical, so both branches fall back
    to the call's name the same way: real id when the model supplied one,
    name otherwise (a fake provider in a test, for instance). Anything else
    would let the two branches disagree about which fallback applies.
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id or message.tool_name or "",
                            "content": _as_text(message.tool_result),
                        }
                    ],
                }
            )
            continue

        blocks: list[dict[str, Any]] = []
        if message.text:
            blocks.append({"type": "text", "text": message.text})
        for call in message.tool_calls:
            blocks.append(
                {
                    "type": "tool_use",
                    # A real Anthropic response always supplies a unique id,
                    # which is threaded through unchanged -- see the module
                    # note. The name-based fallback exists only for a
                    # hand-built ToolCall in a test and is not unique if the
                    # same tool is called twice in one turn with no id; that
                    # combination does not occur on the real API path, where
                    # an id is always present, so it is left as a known
                    # narrow limitation rather than solved with an index that
                    # the paired tool_result message has no way to recover.
                    "id": call.call_id or call.name,
                    "name": call.name,
                    "input": call.arguments,
                }
            )
        if not blocks:
            blocks.append({"type": "text", "text": ""})

        role = "assistant" if message.role == "model" else "user"
        out.append({"role": role, "content": blocks})
    return out


def _as_text(result: Any) -> str:
    """Tool results are returned to Anthropic as a string, unlike Gemini's object."""
    if isinstance(result, str):
        return result
    return json.dumps(result, default=str)


def _to_completion(data: dict[str, Any], model: str) -> Completion:
    if "error" in data:
        raise LlmError(str(data["error"].get("message", data["error"]))[:400])
    if "type" in data and data["type"] == "error":
        raise LlmError(str(data.get("error", data))[:400])

    texts: list[str] = []
    calls: list[ToolCall] = []

    for block in data.get("content", []) or []:
        kind = block.get("type")
        if kind == "text":
            texts.append(block.get("text", ""))
        elif kind == "tool_use":
            calls.append(
                ToolCall(
                    name=block.get("name", ""),
                    arguments=block.get("input") or {},
                    # Carried so the matching tool_result block can reference it
                    # -- Anthropic pairs a result to a call by this id, not by
                    # position, unlike Gemini which matches by function name.
                    call_id=block.get("id"),
                )
            )

    usage_raw = data.get("usage", {}) or {}
    usage = {k: v for k, v in usage_raw.items() if isinstance(v, int)}

    return Completion(
        text="".join(texts).strip(),
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

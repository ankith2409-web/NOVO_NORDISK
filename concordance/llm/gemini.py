"""Gemini provider, over the REST API.

Uses ``urllib`` rather than an SDK: the surface needed here is one POST with a
JSON body, and avoiding a dependency keeps the install trivial and the
behaviour inspectable.

Temperature defaults to 0. Draft EU GMP Annex 22 expects AI used in
GMP-critical work to behave deterministically and to record which model
produced each output, and a documentation generator has no use for creative
variation anyway -- the same model should yield the same requirement text twice.
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

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

#: Chosen for a long context window and fast, inexpensive responses. The whole
#: semantic graph of a large model fits comfortably inside 1M tokens.
DEFAULT_MODEL = "gemini-3.6-flash"


class GeminiProvider:
    """Calls Google's Generative Language API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout: int = 60,
    ) -> None:
        self.model = model
        self.timeout = timeout
        self.name = f"gemini:{model}"
        self._api_key = api_key or _key_from_environment()
        if not self._api_key:
            raise LlmError(
                "No Gemini API key. Set GEMINI_API_KEY in the environment or in a "
                ".env file at the project root."
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
            "contents": [c for m in messages for c in _to_contents(m)],
            "generationConfig": {"temperature": temperature},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        }
                        for t in tools
                    ]
                }
            ]

        data = self._post(payload)
        return _to_completion(data, self.model)

    # -- transport ---------------------------------------------------------

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = _ENDPOINT.format(model=self.model) + f"?key={self._api_key}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            detail = _describe(error)
            # The key can appear in the URL echoed by some error bodies; never
            # let it reach a log or a terminal.
            raise LlmError(
                f"Gemini returned HTTP {error.code}: {_redact(detail, self._api_key)}",
                status=error.code,
            ) from None
        except urllib.error.URLError as error:
            raise LlmUnreachable(f"Could not reach Gemini: {error.reason}") from None
        except json.JSONDecodeError as error:
            raise LlmError(f"Gemini returned a malformed response: {error}") from None


def _key_from_environment() -> str | None:
    """Read the key from the environment, falling back to a local .env file."""
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.environ.get(name)
        if value:
            return value

    # A .env at the project root is the documented place to keep it. Parsed
    # here rather than pulled in as a dependency.
    for candidate in (".env", os.path.join(os.path.dirname(__file__), "..", "..", ".env")):
        try:
            with open(candidate, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    if key.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                        return value.strip().strip("'\"")
        except OSError:
            continue
    return None


def _to_contents(message: Message) -> list[dict[str, Any]]:
    """Translate one message into Gemini's ``contents`` entries."""
    if message.role == "tool":
        return [
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": message.tool_name or "unknown",
                            # Gemini expects an object; wrap scalars and lists.
                            "response": _as_object(message.tool_result),
                        }
                    }
                ],
            }
        ]

    parts: list[dict[str, Any]] = []
    if message.text:
        parts.append({"text": message.text})
    for call in message.tool_calls:
        part: dict[str, Any] = {
            "functionCall": {"name": call.name, "args": call.arguments}
        }
        signature = call.provider_metadata.get("thoughtSignature")
        if signature:
            part["thoughtSignature"] = signature
        parts.append(part)
    if not parts:
        parts.append({"text": ""})

    role = "model" if message.role == "model" else "user"
    return [{"role": role, "parts": parts}]


def _as_object(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    return {"result": result}


def _to_completion(data: dict[str, Any], model: str) -> Completion:
    if "error" in data:
        raise LlmError(str(data["error"].get("message", data["error"]))[:400])

    candidates = data.get("candidates") or []
    if not candidates:
        # A blocked prompt returns no candidate; say so rather than returning "".
        feedback = data.get("promptFeedback", {})
        raise LlmError(f"Gemini returned no candidates. promptFeedback={feedback}")

    content = candidates[0].get("content", {}) or {}
    texts: list[str] = []
    calls: list[ToolCall] = []

    for part in content.get("parts", []) or []:
        if "text" in part:
            texts.append(part["text"])
        elif "functionCall" in part:
            fc = part["functionCall"]
            metadata: dict[str, Any] = {}
            # Gemini 3 requires this to come back unchanged with the history.
            if "thoughtSignature" in part:
                metadata["thoughtSignature"] = part["thoughtSignature"]
            calls.append(
                ToolCall(
                    name=fc.get("name", ""),
                    arguments=fc.get("args") or {},
                    call_id=fc.get("id"),
                    provider_metadata=metadata,
                )
            )

    usage_raw = data.get("usageMetadata", {}) or {}
    usage = {k: v for k, v in usage_raw.items() if isinstance(v, int)}

    return Completion(
        text="".join(texts).strip(),
        tool_calls=tuple(calls),
        usage=usage,
        model=model,
    )


def _describe(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read())
        return str(body.get("error", {}).get("message", body))[:400]
    except Exception:
        return error.reason if isinstance(error.reason, str) else str(error.reason)


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[REDACTED]") if secret else text

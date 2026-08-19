"""AI-written summaries over already-computed drift results.

Everything else in this project that produces requirement text is deliberately
rule-based (see `patterns.py`), because a requirement has to be traceable back
to a fingerprint on every run. A drift report is a different
kind of document: it is read once, by a person deciding whether to worry, and
its value is in how quickly that person understands what changed and why it
matters. That is a summarisation problem, not a specification problem, and an
LLM is a legitimate tool for it -- provided the summary never becomes the
record of truth.

So the contract here is narrow. A summary is generated *from* a report that
has already been computed deterministically; it never decides what counts as
drift, and it is never the only place a finding appears -- the
evidence-bearing detail underneath it is what a reviewer should actually
check. Every summary function returns a `Narrative` that names the provider
it came from and carries the disclaimer, so nothing can present an LLM's
prose as if it were computed fact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from concordance.llm.base import LlmError, LlmProvider, Message

#: Shown wherever a summary is rendered, so it is never mistaken for the
#: evidence-backed report it was generated from.
DISCLAIMER = (
    "AI-generated summary — verify against the evidence and figures below."
)

#: Reports of this size and past it stop being something a summary can
#: usefully compress in a single screenful of prose; asking for one anyway
#: just spends quota restating a report at length.
_MAX_ITEMS = 40


@dataclass(frozen=True)
class Narrative:
    text: str
    provider: str
    disclaimer: str = DISCLAIMER


_DRIFT_SYSTEM = (
    "You summarise structural change reports for a Power BI semantic model. "
    "You are given the exact, already-computed list of changes -- do not invent, "
    "guess at, or add any change, table, or measure that is not present in the "
    "data you were given. Write two to four short sentences a business reader "
    "can skim in a meeting: what changed, how much of it looks like it affects "
    "signed-off requirements, and anything that stands out. Plain prose, no "
    "headings, no bullet points, no markdown."
)


def _facts(payload: dict[str, Any], *, items_key: str, item_limit: int) -> str:
    """A compact JSON view of the report, trimmed so the prompt stays small.

    Trimming truncates the list, not the counts -- the model is always told
    the true totals, so it cannot describe a partial view as the whole
    picture.
    """
    trimmed = dict(payload)
    items = trimmed.get(items_key, [])
    if len(items) > item_limit:
        trimmed[items_key] = items[:item_limit]
        trimmed[f"_{items_key}_truncated_from"] = len(items)
    return json.dumps(trimmed, default=str)


def summarize_drift(drift_result: dict[str, Any], provider: LlmProvider) -> Narrative:
    """Narrate a `/api/drift` payload. Raises `LlmError` on provider failure."""
    facts = _facts(drift_result, items_key="changes", item_limit=_MAX_ITEMS)
    completion = provider.complete(
        [Message(role="user", text=f"Drift report as JSON:\n{facts}")],
        system=_DRIFT_SYSTEM,
    )
    return Narrative(text=completion.text.strip(), provider=provider.name)


__all__ = ["Narrative", "DISCLAIMER", "summarize_drift", "LlmError"]

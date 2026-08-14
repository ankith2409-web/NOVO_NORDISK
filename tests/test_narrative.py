"""AI summaries over drift and reconcile: opt-in, evidence-only, non-fatal.

Three things matter enough to pin down here. The prompt is built only from
the already-computed report -- nothing here reads the graph, so there is no
path for the model to describe an object it was not told about. A summary is
never requested unless asked for, so drift/reconcile keep working with no LLM
key configured at all. And a failed summary degrades to an explained absence,
never a broken report -- the report underneath is the thing that has to keep
working.
"""

from __future__ import annotations

import json

import pytest

from concordance.generate import narrative
from concordance.llm.base import LlmError
from concordance.llm.fake import FakeProvider, says


def test_a_drift_summary_is_generated_from_the_report_alone() -> None:
    provider = FakeProvider(script=[says("Two measures changed; one is a rename.")])
    report = {
        "before": "v1",
        "after": "v2",
        "has_drift": True,
        "changes": [{"node_id": "A", "kind": "changed", "summary": "filter changed"}],
    }

    found = narrative.summarize_drift(report, provider)

    assert found.text == "Two measures changed; one is a rename."
    assert found.provider == "fake"
    assert found.disclaimer == narrative.DISCLAIMER
    # The prompt carries the real report, not a paraphrase of it -- so nothing
    # the model says can describe a change that was not actually computed.
    sent = provider.calls[0]["messages"][0].text
    assert "filter changed" in sent
    assert "A" in sent


def test_a_reconcile_summary_is_generated_from_the_report_alone() -> None:
    provider = FakeProvider(script=[says("Most metrics agree; one diverges.")])
    report = {
        "model": "QC",
        "warehouse": "wh.duckdb",
        "comparisons": [{"metric": "OOS Rate", "verdict": "divergent"}],
    }

    found = narrative.summarize_reconcile(report, provider)

    assert found.text == "Most metrics agree; one diverges."
    sent = provider.calls[0]["messages"][0].text
    assert "OOS Rate" in sent
    assert "divergent" in sent


def test_a_huge_change_list_is_truncated_but_the_true_count_survives() -> None:
    provider = FakeProvider(script=[says("summary")])
    many = [{"node_id": f"n{i}", "kind": "changed"} for i in range(100)]
    report = {"changes": many}

    narrative.summarize_drift(report, provider)

    sent = json.loads(provider.calls[0]["messages"][0].text.split(":\n", 1)[1])
    assert len(sent["changes"]) == narrative._MAX_ITEMS
    assert sent["_changes_truncated_from"] == 100


def test_a_provider_failure_raises_rather_than_returning_placeholder_text() -> None:
    class Failing:
        name = "failing"

        def complete(self, *args, **kwargs):
            raise LlmError("no key configured", status=401)

    with pytest.raises(LlmError):
        narrative.summarize_drift({"changes": []}, Failing())

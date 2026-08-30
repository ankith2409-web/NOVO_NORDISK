"""Recording what a person decided, and knowing when it stopped applying.

The Review queue had no Accept and no Reject, because nothing would have stored
the answer. What is added here is not a status field. The property worth having
is that a decision is bound to the fingerprints of what it was made about, so
when the DAX behind a measure changes the sign-off stops applying by itself.

That is the difference between an audit trail and an approved column. An
approved column silently carries an old approval onto new logic, which is
precisely the failure this project exists to prevent -- so most of what follows
tests that carrying-over cannot happen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.review.decisions import DecisionLog, Status, Verdict


@pytest.fixture
def log(tmp_path: Path) -> DecisionLog:
    return DecisionLog.open(tmp_path / "decisions.jsonl")


# -- the property the whole design turns on -----------------------------------

def test_a_decision_stops_applying_when_the_logic_it_covered_changes(
    log: DecisionLog,
) -> None:
    """The reason decisions are bound to fingerprints rather than stored as a flag.

    Nobody has to notice the change and revoke anything: the comparison happens
    every time the question is asked, against the fingerprints as derived now.
    """
    log.record("REQ-B-1", Verdict.ACCEPTED, ("fp-of-the-original-dax",))

    assert log.standing("REQ-B-1", ("fp-of-the-original-dax",)).status is Status.DECIDED
    assert log.standing("REQ-B-1", ("fp-after-someone-edited-it",)).status is Status.STALE


def test_a_stale_decision_is_reported_not_erased(log: DecisionLog) -> None:
    """"This was accepted, and no longer covers the current definition" is a
    more useful statement than either "approved" or nothing at all."""
    log.record("REQ-B-1", Verdict.ACCEPTED, ("old",), note="checked with QA")
    standing = log.standing("REQ-B-1", ("new",))

    assert standing.status is Status.STALE
    assert standing.latest is not None
    assert standing.latest.note == "checked with QA"


def test_reordered_evidence_is_not_a_change(log: DecisionLog) -> None:
    """Evidence order is an artefact of how derivation walked the graph. Treating
    a reordering as a change would expire good decisions for no reason, and
    train people to ignore staleness."""
    log.record("REQ-B-1", Verdict.ACCEPTED, ("a", "b"))
    assert log.standing("REQ-B-1", ("b", "a")).status is Status.DECIDED


def test_an_undecided_requirement_is_open_rather_than_missing(log: DecisionLog) -> None:
    standing = log.standing("REQ-B-never-seen", ("a",))
    assert standing.status is Status.OPEN
    assert standing.latest is None


# -- the trail ----------------------------------------------------------------

def test_a_later_decision_supersedes_without_erasing(log: DecisionLog) -> None:
    """What separates a trail from a mutable field. "Accepted in March, rejected
    in April, accepted again in May after the measure was rewritten" is the
    history a regulated industry is required to produce."""
    log.record("REQ-B-1", Verdict.ACCEPTED, ("v1",), author="ankith")
    log.record("REQ-B-1", Verdict.REJECTED, ("v1",), note="wrong denominator")
    log.record("REQ-B-1", Verdict.ACCEPTED, ("v2",), note="rewritten")

    standing = log.standing("REQ-B-1", ("v2",))
    assert standing.status is Status.DECIDED
    assert standing.verdict == "accepted"
    assert [d.verdict for d in standing.history] == [
        Verdict.ACCEPTED,
        Verdict.REJECTED,
        Verdict.ACCEPTED,
    ]


def test_decisions_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    DecisionLog.open(path).record("REQ-B-1", Verdict.ACCEPTED, ("fp",))

    reopened = DecisionLog.open(path)
    assert reopened.standing("REQ-B-1", ("fp",)).status is Status.DECIDED


def test_every_decision_is_timestamped(log: DecisionLog) -> None:
    decision = log.record("REQ-B-1", Verdict.ACCEPTED, ("fp",))
    assert decision.at.endswith("+00:00"), "UTC, so two machines' logs collate"


def test_one_requirements_decisions_do_not_leak_into_another(log: DecisionLog) -> None:
    log.record("REQ-B-1", Verdict.ACCEPTED, ("fp",))
    assert log.standing("REQ-B-2", ("fp",)).status is Status.OPEN


# -- honesty about who ---------------------------------------------------------

def test_the_author_is_recorded_as_a_claim_rather_than_a_fact(log: DecisionLog) -> None:
    """This server has no accounts and a shared token is not a person. The field
    name has to say that, or a reader will take it for authentication."""
    log.record("REQ-B-1", Verdict.ACCEPTED, ("fp",), author="ankith")
    stored = log.path.read_text(encoding="utf-8")
    assert "author_claimed" in stored
    assert '"author"' not in stored


def test_an_unnamed_author_is_labelled_rather_than_left_blank(log: DecisionLog) -> None:
    decision = log.record("REQ-B-1", Verdict.ACCEPTED, ("fp",), author="   ")
    assert decision.author == "unattributed"


# -- surviving what a real file does ------------------------------------------

def test_a_truncated_final_line_does_not_lose_the_history(tmp_path: Path) -> None:
    """What a crash mid-append leaves behind. Losing the whole trail to a half
    written last line would be a poor trade for strictness."""
    path = tmp_path / "decisions.jsonl"
    log = DecisionLog.open(path)
    log.record("REQ-B-1", Verdict.ACCEPTED, ("fp",))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"requirement_id": "REQ-B-2", "verdi')

    reopened = DecisionLog.open(path)
    assert reopened.standing("REQ-B-1", ("fp",)).status is Status.DECIDED
    assert reopened.counts()["decisions"] == 1


def test_a_missing_file_is_an_empty_log_not_an_error(tmp_path: Path) -> None:
    log = DecisionLog.open(tmp_path / "nested" / "never-written.jsonl")
    assert log.counts()["decisions"] == 0
    assert log.standing("REQ-B-1", ("fp",)).status is Status.OPEN


def test_the_file_is_created_on_first_write(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "decisions.jsonl"
    DecisionLog.open(path).record("REQ-B-1", Verdict.ACCEPTED, ("fp",))
    assert path.exists()


def test_the_format_is_one_self_describing_object_per_line(log: DecisionLog) -> None:
    """So the trail can be read by anything, including by hand, without this
    code. An audit record nobody else can open is not much of a record."""
    import json

    log.record("REQ-B-1", Verdict.ACCEPTED, ("fp",), note="ok")
    log.record("REQ-B-2", Verdict.REJECTED, ("fp2",))

    lines = log.path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)
        assert set(parsed) >= {"requirement_id", "verdict", "bound_fingerprints", "at"}


def test_a_requirement_resting_on_nothing_is_handled(log: DecisionLog) -> None:
    """Some requirements are derived from the model's shape rather than from a
    fingerprinted object. They can still be decided; the decision simply cannot
    go stale, which is correct -- there is nothing under it to change."""
    log.record("REQ-B-scope", Verdict.ACCEPTED, ())
    assert log.standing("REQ-B-scope", ()).status is Status.DECIDED


# -- through the API, where the guarantee has to survive a caller -------------

def _context(tmp_path: Path):
    """A real model with a real decision log attached."""
    from concordance.adapters.tmdl import TmdlAdapter
    from concordance.graph.csg import SemanticGraph
    from concordance.web.api import ApiContext

    model = Path("data/models/QualityControl.SemanticModel")
    if not model.exists():
        pytest.skip(f"model not present: {model}")
    return ApiContext(
        graph=SemanticGraph(TmdlAdapter().extract(str(model))),
        decisions=tmp_path / "decisions.jsonl",
    )


def _first_pending(context) -> dict:
    from concordance.web.api import review

    payload = review(context, {})
    if not payload["pending"]:
        pytest.skip("this model has nothing needing review")
    return payload["pending"][0]


def test_the_queue_reports_where_each_item_stands(tmp_path: Path) -> None:
    from concordance.web.api import decide, review

    context = _context(tmp_path)
    item = _first_pending(context)
    assert item["standing"]["status"] == "open"

    decide(context, {"requirement_id": item["id"], "verdict": "accepted"})

    after = review(context, {})
    decided = next(p for p in after["pending"] if p["id"] == item["id"])
    assert decided["standing"]["status"] == "decided"
    assert after["decided"] == 1
    assert after["open"] == after["count"] - 1


def test_the_caller_cannot_state_what_it_is_deciding_about(tmp_path: Path) -> None:
    """The one thing this record exists to make impossible.

    A client that could supply the fingerprints could accept a statement while
    recording that it had approved something else entirely. The server derives
    them instead, so what is written down is always what was on screen.
    """
    from concordance.review.decisions import DecisionLog
    from concordance.web.api import decide

    context = _context(tmp_path)
    item = _first_pending(context)

    decide(
        context,
        {
            "requirement_id": item["id"],
            "verdict": "accepted",
            "bound_fingerprints": ["something-the-caller-made-up"],
        },
    )

    recorded = DecisionLog.open(context.decisions).history_for(item["id"])[0]
    assert "something-the-caller-made-up" not in recorded.bound_fingerprints
    assert set(recorded.bound_fingerprints) == {
        e["fingerprint"] for e in item["evidence"]
    }


def test_deciding_is_refused_when_no_log_was_configured(tmp_path: Path) -> None:
    """A control that looked like it filed an approval while storing nothing
    would be worse than no control at all."""
    from concordance.web.api import ApiError, decide, review

    context = _context(tmp_path)
    context.decisions = None

    assert review(context, {})["can_decide"] is False
    with pytest.raises(ApiError) as caught:
        decide(context, {"requirement_id": "REQ-B-1", "verdict": "accepted"})
    assert caught.value.status == 501
    assert "--decisions" in caught.value.message


def test_an_unknown_requirement_is_refused(tmp_path: Path) -> None:
    from concordance.web.api import ApiError, decide

    with pytest.raises(ApiError) as caught:
        decide(_context(tmp_path), {"requirement_id": "REQ-B-nope", "verdict": "accepted"})
    assert caught.value.status == 404


def test_an_unknown_verdict_lists_the_ones_that_exist(tmp_path: Path) -> None:
    from concordance.web.api import ApiError, decide

    context = _context(tmp_path)
    item = _first_pending(context)
    with pytest.raises(ApiError) as caught:
        decide(context, {"requirement_id": item["id"], "verdict": "looks-fine-to-me"})
    assert caught.value.status == 400
    assert "accepted" in caught.value.message


def test_an_oversized_note_is_refused_rather_than_stored(tmp_path: Path) -> None:
    from concordance.web.api import ApiError, decide

    context = _context(tmp_path)
    item = _first_pending(context)
    with pytest.raises(ApiError):
        decide(
            context,
            {"requirement_id": item["id"], "verdict": "accepted", "note": "x" * 5000},
        )


# -- what an audit trail is required to carry ---------------------------------
#
# This log exists inside a pharmaceutical company, which puts it under 21 CFR
# Part 11: a secure, computer-generated, time-stamped trail recording who did
# what, when, and why, where a change never obscures what it replaced. Checked
# against that rather than against what seemed sufficient.

def test_a_rejection_must_say_why(tmp_path) -> None:
    """Part 11's fourth field, and the one that was missing.

    The interface demanded a reason and the API did not, so a decision recorded
    any other way went into the trail with an empty note -- and the reason is
    exactly what the next reader needs to avoid starting the investigation
    again.
    """
    from concordance.cli import _load
    from concordance.generate.requirements import Kind, RequirementDeriver
    from concordance.web import api

    graph = _load("data/models/QualityControl.SemanticModel")
    context = api.ApiContext(graph=graph, decisions=tmp_path / "d.jsonl")
    rid = next(
        r.id for r in RequirementDeriver(graph).derive() if r.kind is Kind.BUSINESS
    )
    for verdict in ("rejected", "corrected"):
        with pytest.raises(api.ApiError) as raised:
            api.decide(context, {"requirement_id": rid, "verdict": verdict})
        assert raised.value.status == 400
        assert "why" in raised.value.message


def test_an_acceptance_needs_no_prose(tmp_path) -> None:
    """"The statement stands as written" is the reason.

    Demanding a sentence for it would train reviewers to type "ok", which is
    worse than an empty note: it looks like a reason and carries none.
    """
    from concordance.cli import _load
    from concordance.generate.requirements import Kind, RequirementDeriver
    from concordance.web import api

    graph = _load("data/models/QualityControl.SemanticModel")
    context = api.ApiContext(graph=graph, decisions=tmp_path / "d.jsonl")
    rid = next(
        r.id for r in RequirementDeriver(graph).derive() if r.kind is Kind.BUSINESS
    )
    out = api.decide(context, {"requirement_id": rid, "verdict": "accepted"})
    assert out["standing"]["verdict"] == "accepted"


def test_the_trail_records_who_what_when_and_why(tmp_path) -> None:
    """All four fields Part 11 names, in one entry."""
    log = DecisionLog.open(tmp_path / "d.jsonl")
    entry = log.record(
        requirement_id="REQ-B-1",
        verdict=Verdict.REJECTED,
        bound_fingerprints=("abc",),
        note="The relationship is inactive, so this is not how the number is computed.",
        author="Anna",
        author_verified=True,
    )
    assert entry.author == "Anna" and entry.author_verified  # who, and whether it is a fact
    assert entry.verdict is Verdict.REJECTED  # what
    assert entry.at.endswith("+00:00")  # when, server-generated and in UTC
    assert entry.note  # why


def test_the_timestamp_cannot_come_from_the_caller(tmp_path) -> None:
    """"Computer-generated" is the phrase in the rule.

    A trail whose times are supplied by whoever is being audited is not a trail.
    """
    log = DecisionLog.open(tmp_path / "d.jsonl")
    first = log.record("REQ-B-1", Verdict.ACCEPTED, ("abc",), author="Anna")
    assert "at" not in DecisionLog.record.__code__.co_varnames, (
        "record() accepts a timestamp from its caller"
    )
    assert first.at


def test_a_later_decision_never_obscures_an_earlier_one(tmp_path) -> None:
    """Part 11: "record changes shall not obscure previously recorded information".

    An append-only file gives this, and the history is what the interface shows
    -- so a reviewer sees that a statement was rejected before it was accepted,
    not merely that it is accepted now.
    """
    path = tmp_path / "d.jsonl"
    log = DecisionLog.open(path)
    log.record("REQ-B-1", Verdict.REJECTED, ("abc",), note="wrong", author="Anna")
    log.record("REQ-B-1", Verdict.ACCEPTED, ("abc",), author="Bob")

    history = DecisionLog.open(path).history_for("REQ-B-1")
    assert [d.verdict for d in history] == [Verdict.REJECTED, Verdict.ACCEPTED]
    assert history[0].note == "wrong", "the superseded entry lost its reason"
    assert path.read_text().count("\n") == 2, "an entry was rewritten rather than appended"


def test_a_queue_whose_log_is_temporary_says_so(tmp_path) -> None:
    """ALCOA+ requires a record to be *enduring*. When it is not, the person
    making it is the one who needs to know -- before they decide, not after.

    The server cannot detect this: a path looks identical whether or not the
    filesystem under it survives a restart. So the operator declares it, and
    the flag reaches the queue.
    """
    from concordance.cli import _load
    from concordance.web import api

    graph = _load("data/models/QualityControl.SemanticModel")
    ephemeral = api.ApiContext(
        graph=graph, decisions=tmp_path / "d.jsonl", decisions_reset=True
    )
    durable = api.ApiContext(graph=graph, decisions=tmp_path / "d.jsonl")

    assert api.review(ephemeral, {})["decisions_reset"] is True
    assert api.review(durable, {})["decisions_reset"] is False
    # It never contradicts can_decide: the queue is writable either way.
    assert api.review(ephemeral, {})["can_decide"] is True

"""The "What changed since" section that both documents carry.

The Drift tab already answers this, and keeps answering it -- but only for
whoever is sitting in front of the tool. The document is the thing that gets
sent to someone, and "what changed since the version I approved" is the first
question its reader asks. So the same comparison is carried into the BRD and the
FRD, said in words rather than in fingerprints.

What is asserted here is mostly about *wording and placement*, because that is
where this can fail. The comparison itself is tested in test_drift.py and is not
re-tested; what these check is that the document does not quietly lose changes,
does not attribute a change to a requirement that is not in the document the
reader is holding, and does not say "nothing changed" when it was simply never
given anything to compare against -- which is a different claim.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.drift import snapshot as snap
from concordance.drift.compare import compare
from concordance.generate import document as doc
from concordance.generate.requirements import Kind
from concordance.graph.csg import SemanticGraph

V1 = Path("data/models/ClinicalTrialSafety.SemanticModel")
V2 = Path("data/models/ClinicalTrialSafety_v2.SemanticModel")


def _graph(path: Path) -> SemanticGraph:
    if not path.exists():
        pytest.skip(f"model not present: {path}")
    return SemanticGraph(TmdlAdapter().extract(str(path)))


@pytest.fixture(scope="module")
def report():
    after, before = _graph(V1), _graph(V2)
    return compare(
        snap.take(before, label="v2"),
        snap.take(after, label=after.model.name),
        after_graph=after,
    )


@pytest.fixture(scope="module")
def graph():
    return _graph(V1)


def _built(graph, report, kind=Kind.FUNCTIONAL):
    return doc.build(graph, kind, drift=report)


def test_no_baseline_says_nothing_at_all(graph):
    """Silence, not a section claiming nothing changed.

    These are different statements and confusing them is the failure worth
    guarding: a document that says "nothing changed" when it was never given a
    previous version to compare against is asserting something it does not know.
    """
    built = doc.build(graph, Kind.FUNCTIONAL)
    assert built.changes == ()
    assert built.changed_from == ""
    assert "What changed since" not in doc.to_markdown(built)


def test_every_change_reaches_the_document(graph, report):
    """No change is silently dropped between the report and the page."""
    built = _built(graph, report)
    assert len(built.changes) == len(report.changes)


def test_changed_definitions_come_first(graph, report):
    """The ones that can have moved a number, before the ones that cannot."""
    built = _built(graph, report)
    kinds = [c.change for c in built.changes]
    assert kinds == sorted(kinds, key=lambda k: {"changed": 0, "removed": 1, "added": 2}.get(k, 3))


def test_only_this_document_s_requirements_are_cited(graph, report):
    """An FRD must not send its reader after a REQ-B id it does not contain.

    Both documents are derived from the same model and a change lands on both
    kinds of requirement, so the unfiltered mapping cites ids from the other
    document -- which reads, to someone searching for it, as a broken reference.
    """
    frd = _built(graph, report, Kind.FUNCTIONAL)
    brd = _built(graph, report, Kind.BUSINESS)
    cited_by_frd = {id_ for c in frd.changes for id_ in c.affects}
    cited_by_brd = {id_ for c in brd.changes for id_ in c.affects}

    assert cited_by_frd, "the FRD should cite the requirements changes land on"
    assert all(id_.startswith("REQ-F-") for id_ in cited_by_frd)
    assert all(id_.startswith("REQ-B-") for id_ in cited_by_brd)
    assert {r.id for r in frd.requirements} >= cited_by_frd
    assert {r.id for r in brd.requirements} >= cited_by_brd


def test_a_rename_is_not_reported_as_needing_a_re_check(graph, report):
    """Renames are separated, and said to need no re-validation.

    The whole value of proving a rename is a rename is that a reviewer can skip
    it. A section that lists it beside a real edit throws that away.
    """
    built = _built(graph, report)
    for change in built.changes:
        if change.change == "renamed":
            assert "nothing here needs re-checking" in change.means


def test_markdown_says_it_in_plain_words(graph, report):
    """The section reads as prose, and names the version compared against."""
    text = doc.to_markdown(_built(graph, report))
    assert "## What changed since v2" in text
    assert "Compared with v2:" in text
    # The consequence, not the mechanism: no fingerprints, no node ids, no
    # enum names leaking into a document a business reader signs.
    section = text.split("## What changed since v2", 1)[1].split("\n## ", 1)[0]
    assert "fingerprint" not in section.lower()
    assert "node_id" not in section
    assert "ChangeKind" not in section
    assert "report a different number" in section


def test_the_meaning_is_stated_once_per_group_not_once_per_row(graph, report):
    """The repetition that makes generated documents unreadable.

    Four changed measures under one sentence is a paragraph; the same sentence
    four times down a table column is noise, and it was a table until it was
    read back.
    """
    section = (
        doc.to_markdown(_built(graph, report))
        .split("## What changed since v2", 1)[1]
        .split("\n## ", 1)[0]
    )
    sentence = "These are the ones to check against what was agreed."
    assert section.count(sentence) == 1

    groups = doc.changes_groups(_built(graph, report))
    assert [heading.split(" (")[0] for heading, _, _ in groups][0] == "Changed definitions"
    assert sum(len(rows) for _, _, rows in groups) == len(report.changes)


def test_an_identical_model_says_so_rather_than_showing_an_empty_section(graph):
    """An empty heading reads as a rendering bug, not as good news."""
    unchanged = compare(
        snap.take(graph, label="yesterday"),
        snap.take(graph, label=graph.model.name),
        after_graph=graph,
    )
    built = doc.build(graph, Kind.FUNCTIONAL, drift=unchanged)
    assert built.changes == ()
    text = doc.to_markdown(built)
    assert "## What changed since yesterday" in text
    assert "Nothing." in text


def test_the_word_document_carries_the_same_section(graph, report):
    """Two renderers, one document -- including this."""
    pytest.importorskip("docx")
    from concordance.generate import word

    built = _built(graph, report)
    rendered = word.render(built)
    headings = [p.text for p in rendered.paragraphs if p.style.name.startswith("Heading")]
    assert "What changed since v2" in headings
    assert any(h.startswith("Changed definitions (") for h in headings)

    bullets = [
        p.text for p in rendered.paragraphs if p.style.name == "List Bullet"
    ]
    assert any("Serious Adverse Events" in b for b in bullets)

"""Word export and the evidence pack.

Rendering cannot be checked visually here -- LibreOffice in this environment
fails to convert even a one-paragraph document -- so the document is verified
structurally instead: heading levels Word will build a navigation pane from, a
traceability row per requirement, DAX in a monospaced run, and no Markdown
markers surviving into text someone is about to circulate for signature.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from docx import Document as WordDocument

from concordance.adapters.tmdl import TmdlAdapter
from concordance.generate import auditpack, word
from concordance.generate import document as doc
from concordance.generate.requirements import Kind
from concordance.generate.word import _split_markup
from concordance.graph.csg import SemanticGraph

MODEL = Path("data/models/ClinicalTrialSafety.SemanticModel")


@pytest.fixture(scope="module")
def graph() -> SemanticGraph:
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return SemanticGraph(TmdlAdapter().extract(str(MODEL)))


@pytest.fixture(scope="module")
def brd_path(graph: SemanticGraph, tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("word") / "brd.docx"
    return word.write(doc.build(graph, Kind.BUSINESS), out)


# -- the markup splitter -----------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("plain text", [("plain text", "plain")]),
        ("a **bold** b", [("a ", "plain"), ("bold", "bold"), (" b", "plain")]),
        ("use `CODE` here", [("use ", "plain"), ("CODE", "code"), (" here", "plain")]),
        (
            "**A** and `B`",
            [("A", "bold"), (" and ", "plain"), ("B", "code")],
        ),
        # An unmatched marker is text, not the start of formatting.
        ("2 ** 3 is not bold", [("2 ** 3 is not bold", "plain")]),
    ],
)
def test_markup_splits_correctly(text: str, expected: list) -> None:
    assert _split_markup(text) == expected


# -- the rendered document ---------------------------------------------------

def test_the_file_opens_as_a_valid_word_document(brd_path: Path) -> None:
    reopened = WordDocument(str(brd_path))
    assert len(reopened.paragraphs) > 50
    assert reopened.tables


def test_headings_use_built_in_styles(brd_path: Path) -> None:
    """Word builds its navigation pane from these; custom styles would not appear."""
    styles = {p.style.name for p in WordDocument(str(brd_path)).paragraphs}
    assert "Title" in styles
    assert "Heading 1" in styles
    assert "Heading 2" in styles


def test_no_markdown_markers_survive_into_the_text(brd_path: Path) -> None:
    """A document going out for signature must not contain literal asterisks."""
    for paragraph in WordDocument(str(brd_path)).paragraphs:
        assert "**" not in paragraph.text
        assert "`" not in paragraph.text


def test_bold_markup_becomes_actual_bold_runs(brd_path: Path) -> None:
    document = WordDocument(str(brd_path))
    bold = [r for p in document.paragraphs for r in p.runs if r.bold and r.text.strip()]
    assert len(bold) > 20


def test_dax_is_rendered_monospaced(brd_path: Path) -> None:
    document = WordDocument(str(brd_path))
    mono = [
        r
        for p in document.paragraphs
        for r in p.runs
        if r.font.name == "Consolas" and "CALCULATE" in r.text
    ]
    assert mono, "DAX expressions should be visually distinct from prose"


def test_traceability_matrix_has_a_row_per_requirement(
    graph: SemanticGraph, brd_path: Path
) -> None:
    built = doc.build(graph, Kind.BUSINESS)
    table = WordDocument(str(brd_path)).tables[0]

    assert len(table.rows) == len(built.requirements) + 1  # + header
    headers = [c.text for c in table.rows[0].cells]
    assert headers == ["Requirement", "Bound to", "Fingerprint", "Confidence"]

    ids_in_table = {row.cells[0].text for row in table.rows[1:]}
    assert ids_in_table == {r.id for r in built.requirements}


def test_the_review_queue_appears_before_the_requirements(brd_path: Path) -> None:
    paragraphs = [p.text for p in WordDocument(str(brd_path)).paragraphs]
    queue = paragraphs.index("Awaiting human confirmation")
    first_section = next(i for i, t in enumerate(paragraphs) if t.startswith("1. "))
    assert queue < first_section


def test_both_document_kinds_render(graph: SemanticGraph, tmp_path: Path) -> None:
    for kind in (Kind.BUSINESS, Kind.FUNCTIONAL):
        path = word.write(doc.build(graph, kind), tmp_path / f"{kind.value}.docx")
        assert WordDocument(str(path)).paragraphs


# -- the evidence pack -------------------------------------------------------

@pytest.fixture(scope="module")
def pack(graph: SemanticGraph, tmp_path_factory):
    return auditpack.build(graph, tmp_path_factory.mktemp("pack"))


def test_the_pack_contains_every_expected_artefact(pack) -> None:
    names = {p.name for p in pack.files}
    assert "MANIFEST.json" in names
    assert "README.txt" in names
    assert any(n.endswith(".BRD.docx") for n in names)
    assert any(n.endswith(".FRD.docx") for n in names)
    assert any(n.endswith(".BRD.md") for n in names)
    assert any(n.endswith(".fingerprints.json") for n in names)


def test_the_manifest_records_what_was_read_and_by_which_version(pack) -> None:
    manifest = json.loads((pack.directory / "MANIFEST.json").read_text())

    assert manifest["tool"]["name"] == "concordance"
    assert manifest["tool"]["version"]
    assert manifest["model"]["name"] == "ClinicalTrialSafety"
    assert manifest["model"]["measures"] == 24
    assert manifest["requirements"]["total"] == pack.requirement_count


def test_the_manifest_states_its_own_limitations(pack) -> None:
    """A pack that hid what it could not read would misrepresent its completeness."""
    manifest = json.loads((pack.directory / "MANIFEST.json").read_text())
    limitations = manifest["limitations"]

    assert "unresolved_references" in limitations
    assert "features_not_extracted" in limitations
    assert manifest["requirements"]["awaiting_human_confirmation"] == pack.needs_review


def test_the_fingerprint_manifest_makes_the_pack_verifiable(
    pack, graph: SemanticGraph
) -> None:
    """The property that makes this evidence rather than assertion."""
    from concordance.drift import snapshot as snap
    from concordance.drift.compare import compare

    stored = snap.Snapshot.load(
        next(p for p in pack.files if p.name.endswith(".fingerprints.json"))
    )
    # Recomputing from the same model must agree with what the pack recorded.
    report = compare(stored, snap.take(graph, "recheck"))
    assert not report.has_drift


def test_the_readme_explains_how_to_verify_the_pack(pack) -> None:
    readme = (pack.directory / "README.txt").read_text()
    assert "concordance drift" in readme
    assert "ClinicalTrialSafety" in readme
    # It must not overclaim regulatory standing.
    assert "quality function" in readme


def test_the_pack_counts_match_the_documents_it_contains(
    pack, graph: SemanticGraph
) -> None:
    brd = doc.build(graph, Kind.BUSINESS)
    frd = doc.build(graph, Kind.FUNCTIONAL)
    assert pack.requirement_count == len(brd.requirements) + len(frd.requirements)

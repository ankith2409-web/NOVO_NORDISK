"""TMDL parsing and extraction.

A hazard worth naming: the same author wrote both the sample TMDL model and the
parser that reads it, so tests that only exercise that model would happily pass
on a shared misunderstanding of the format. The conformance tests below are
therefore written against syntax from Microsoft's TMDL specification that the
sample model deliberately does not use -- escaped quotes in names,
space-indentation, explicit cardinality, bidirectional cross-filtering -- so the
parser is checked against the format rather than against its author's habits.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter, _split_reference, parse
from concordance.graph.csg import SemanticGraph

MODEL = Path("data/models/ClinicalTrialSafety.SemanticModel")


def _available() -> Path:
    if not MODEL.exists():
        pytest.skip(f"TMDL model not present: {MODEL}")
    return MODEL


@pytest.fixture(scope="module")
def clinical() -> SemanticGraph:
    return SemanticGraph(TmdlAdapter().extract(str(_available())))


# -- grammar conformance, independent of the sample model --------------------

def test_object_declaration_and_property_syntax() -> None:
    nodes = parse(
        "table Sales\n"
        "\tcolumn Quantity\n"
        "\t\tdataType: int64\n"
        "\t\tisHidden\n"
        "\t\tsourceColumn: Quantity\n"
    )
    table = nodes[0]
    assert (table.kind, table.name) == ("table", "Sales")

    column = table.child("column")[0]
    assert column.properties["dataType"] == "int64"
    assert column.properties["sourceColumn"] == "Quantity"
    # A bare property name means true.
    assert column.properties["isHidden"] == "true"


def test_space_indentation_parses_like_tab_indentation() -> None:
    """The spec says tabs, but hand-edited files routinely arrive with spaces."""
    tabbed = parse("table T\n\tcolumn C\n\t\tdataType: string\n")
    spaced = parse("table T\n    column C\n        dataType: string\n")
    assert tabbed[0].child("column")[0].properties == (
        spaced[0].child("column")[0].properties
    )


def test_quoted_names_and_doubled_quote_escape() -> None:
    """A quote inside a name is escaped by doubling it."""
    nodes = parse("table 'Bob''s Data'\n\tcolumn 'Product Key'\n\t\tdataType: string\n")
    assert nodes[0].name == "Bob's Data"
    assert nodes[0].child("column")[0].name == "Product Key"


def test_description_attaches_to_the_following_declaration() -> None:
    nodes = parse(
        "/// Revenue net of returns.\n"
        "table Sales\n"
        "\t/// Money, before tax.\n"
        "\tmeasure Amount = SUM(Sales[Amount])\n"
    )
    assert nodes[0].description == "Revenue net of returns."
    assert nodes[0].child("measure")[0].description == "Money, before tax."


def test_inline_and_multiline_expressions() -> None:
    nodes = parse(
        "table T\n"
        "\tmeasure Inline = SUM(T[A])\n"
        "\tmeasure Multi =\n"
        "\t\tVAR x = SUM(T[A])\n"
        "\t\tRETURN x * 2\n"
        "\t\tformatString: 0.00\n"
    )
    measures = {m.name: m for m in nodes[0].child("measure")}
    assert measures["Inline"].expression == "SUM(T[A])"

    multi = measures["Multi"]
    assert "VAR x = SUM(T[A])" in multi.expression
    assert "RETURN x * 2" in multi.expression
    # A property line ends the expression rather than being swallowed by it.
    assert "formatString" not in multi.expression


def test_fenced_block_preserves_its_contents() -> None:
    nodes = parse(
        "table T\n"
        "\tpartition P = m\n"
        "\t\tmode: import\n"
        "\t\tsource = ```\n"
        "\t\t\tlet\n"
        "\t\t\t    Source = Table.FromRows({})\n"
        "\t\t\tin\n"
        "\t\t\t    Source\n"
        "\t\t```\n"
    )
    partition = nodes[0].child("partition")[0]
    # `partition P = m` assigns the mode; the M query is the `source` property.
    assert partition.expression == "m"
    assert "let" in partition.properties["source"]
    assert "Source = Table.FromRows({})" in partition.properties["source"]


def test_ref_lines_declare_nothing() -> None:
    """`ref table X` records collection order; it is not a second declaration."""
    nodes = parse("model Model\n\nref table Sales\nref table Product\n")
    assert [n.kind for n in nodes] == ["model"]


def test_column_is_a_property_name_as_well_as_a_keyword() -> None:
    """A hierarchy level's target is written `column: Year`, not `column Year`.

    Treating every occurrence of a declaration keyword as a declaration silently
    stripped every level of its column, which is how this was found.
    """
    nodes = parse(
        "table Calendar\n"
        "\thierarchy Periods\n"
        "\t\tlevel Year\n"
        "\t\t\tcolumn: Year\n"
        "\t\tlevel Month\n"
        "\t\t\tcolumn: 'Month Name'\n"
    )
    levels = nodes[0].child("hierarchy")[0].child("level")
    assert [lv.properties.get("column") for lv in levels] == ["Year", "'Month Name'"]


@pytest.mark.parametrize(
    "reference,expected",
    [
        ("Sales.Amount", ("Sales", "Amount")),
        ("Sales.'Product Key'", ("Sales", "Product Key")),
        ("'Clinical Metrics'.'Patients Enrolled'", ("Clinical Metrics", "Patients Enrolled")),
        ("", ("", "")),
    ],
)
def test_column_references_split_correctly(reference: str, expected: tuple) -> None:
    assert _split_reference(reference) == expected


def test_relationship_properties_the_sample_model_does_not_use(tmp_path: Path) -> None:
    """Explicit cardinality and bidirectional filtering must both be honoured."""
    definition = tmp_path / "M.SemanticModel" / "definition"
    (definition / "tables").mkdir(parents=True)
    (definition / "tables" / "A.tmdl").write_text(
        "table A\n\tcolumn K\n\t\tdataType: int64\n", encoding="utf-8"
    )
    (definition / "tables" / "B.tmdl").write_text(
        "table B\n\tcolumn K\n\t\tdataType: int64\n", encoding="utf-8"
    )
    (definition / "relationships.tmdl").write_text(
        "relationship r1\n"
        "\tfromColumn: A.K\n"
        "\ttoColumn: B.K\n"
        "\tfromCardinality: one\n"
        "\ttoCardinality: one\n"
        "\tcrossFilteringBehavior: bothDirections\n",
        encoding="utf-8",
    )

    model = TmdlAdapter().extract(str(tmp_path / "M.SemanticModel"))
    relationship = model.relationships[0]
    assert relationship.cardinality == "1:1"
    assert relationship.cross_filter == "Both"
    assert relationship.is_active


# -- the clinical model ------------------------------------------------------

def test_clinical_model_extracts_completely(clinical: SemanticGraph) -> None:
    """Counts checked against the .tmdl text, not against a remembered number."""
    summary = clinical.model.summary()
    assert summary["tables"] == 8
    assert summary["columns"] == 56
    assert summary["measures"] == 24
    assert summary["relationships"] == 10
    assert summary["hierarchies"] == 4
    assert sum(len(h.levels) for h in clinical.model.hierarchies) == 11


def test_clinical_model_has_no_unresolved_references(clinical: SemanticGraph) -> None:
    assert clinical.unresolved == []


def test_meddra_hierarchy_is_bound_to_real_columns(clinical: SemanticGraph) -> None:
    hierarchy = next(
        h for h in clinical.model.hierarchies if h.name == "MedDRA Terms"
    )
    assert hierarchy.path == "System Organ Class -> Preferred Term"
    assert [lv.column for lv in hierarchy.levels] == [
        "SystemOrganClass",
        "PreferredTerm",
    ]


def test_inactive_relationships_are_preserved(clinical: SemanticGraph) -> None:
    """Both are deliberate: a role-playing date and an ambiguity-avoiding path."""
    inactive = [r for r in clinical.model.relationships if not r.is_active]
    assert len(inactive) == 2
    pairs = {(r.from_table, r.to_table) for r in inactive}
    assert ("AdverseEvent", "Study") in pairs
    assert ("Patient", "Calendar") in pairs


def test_measure_dependencies_resolve_across_files(clinical: SemanticGraph) -> None:
    """Measures live in one file but reference columns declared in others."""
    measure = next(
        m for m in clinical.model.measures if m.name == "SAE Rate per 100 Patients"
    )
    assert {"Serious Adverse Events", "Patients Enrolled"} <= set(
        measure.depends_on_measures
    )


def test_multiline_dax_survives_extraction(clinical: SemanticGraph) -> None:
    signal = next(m for m in clinical.model.measures if m.name == "Signal Score (PRR)")
    assert "VAR" in signal.expression.upper()
    assert "REMOVEFILTERS" in signal.expression.upper()
    assert len(signal.fingerprint) == 64


def test_calculated_columns_are_detected(clinical: SemanticGraph) -> None:
    calculated = [c for c in clinical.model.columns if c.is_calculated]
    names = {c.name for c in calculated}
    assert {"Days On Treatment", "Days To Report"} <= names


def test_documents_generate_from_the_clinical_model(clinical: SemanticGraph) -> None:
    from concordance.generate import document as doc
    from concordance.generate.requirements import Kind

    brd = doc.build(clinical, Kind.BUSINESS)
    markdown = doc.to_markdown(brd)

    assert "Serious Adverse Events" in markdown
    assert "MedDRA Terms" in markdown
    # Both inactive relationships need a human to state their purpose.
    assert len(brd.review_queue) == 2


def test_missing_model_raises_clearly() -> None:
    with pytest.raises(FileNotFoundError):
        TmdlAdapter().extract("data/models/NoSuchModel.SemanticModel")


def test_a_folder_without_tmdl_files_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="does not look like a TMDL model"):
        TmdlAdapter().extract(str(tmp_path / "empty"))

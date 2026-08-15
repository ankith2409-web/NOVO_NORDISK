"""The applied steps of a Power Query, and why they belong in a document.

`extract_sources` answers "where does this table come from". That is only half
the question. A table loaded from `patients.csv` and then filtered is not
`patients.csv`, and someone reconciling a figure against that file directly
will get a different number with nothing in the document explaining why.

So the tests here care about two things beyond parsing: that a step which can
change how many rows survive is distinguished from one that cannot, and that a
change to the query is visible to drift at all -- which it was not, because a
table's fingerprint used to be taken over its name alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.drift import snapshot as snap
from concordance.drift.compare import compare
from concordance.generate.requirements import RequirementDeriver
from concordance.graph.csg import SemanticGraph
from concordance.normalize.mquery import canonicalise, extract_steps

QUERY = """let
    Source = Csv.Document(File.Contents("batch.csv"), [Delimiter=","]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    Typed = Table.TransformColumnTypes(Promoted, {{"Yield", Int64.Type}}),
    #"Released Only" = Table.SelectRows(Typed, each [Status] = "Released")
in
    #"Released Only\""""


def test_every_step_is_named_in_order() -> None:
    steps = extract_steps(QUERY)
    assert [s.name for s in steps] == ["Source", "Promoted", "Typed", "Released Only"]
    assert [s.ordinal for s in steps] == [0, 1, 2, 3]


def test_the_function_each_step_applies_is_the_outermost_one() -> None:
    """`Csv.Document(File.Contents(...))` is a parse of a read, and the step is
    the parse -- reading the innermost call would name the wrong operation."""
    assert extract_steps(QUERY)[0].function == "Csv.Document"
    assert extract_steps(QUERY)[3].function == "Table.SelectRows"


def test_commas_inside_records_and_lists_do_not_end_a_step() -> None:
    """The case that breaks a naive split: nearly every real step carries a
    record or a list argument, and both are full of commas."""
    steps = extract_steps(QUERY)
    assert steps[2].expression.startswith("Table.TransformColumnTypes")
    assert "Int64.Type" in steps[2].expression


def test_only_steps_that_can_drop_rows_are_flagged() -> None:
    """The flag is a warning that the table is not its source. Promoting a
    header row is the expected shape of reading a file, so flagging it too
    would put the warning on almost every query and make it worthless."""
    by_name = {s.name: s for s in extract_steps(QUERY)}
    assert by_name["Released Only"].changes_row_count is True
    assert by_name["Promoted"].changes_row_count is False
    assert by_name["Typed"].changes_row_count is False


def test_an_unrecognised_function_is_named_not_guessed_at() -> None:
    steps = extract_steps("let A = Foo.Bar(1), B = Table.Distinct(A) in B")
    assert steps[0].function == "Foo.Bar"
    assert steps[0].effect == "", "a description we do not have must not be invented"
    assert steps[0].changes_row_count is False
    assert steps[1].effect


def test_a_query_with_no_let_block_has_no_steps() -> None:
    """Legal M, and it must not be reported as a one-step query it is not."""
    assert extract_steps('Sql.Database("server", "db")') == ()


def test_reindenting_a_query_is_not_a_change() -> None:
    assert canonicalise("let\n  A = 1\nin\n  A") == canonicalise("let A=1 in A")
    assert canonicalise('let A = "x  y" in A') != canonicalise('let A = "x y" in A')


def test_a_comment_is_not_part_of_the_query_identity() -> None:
    assert canonicalise("let // note\n A = 1 in A") == canonicalise("let A = 1 in A")


# -- the reason this is fingerprinted at all ----------------------------------

def write(root: Path, relative: str, text: str) -> None:
    path = root / "definition" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build(root: Path, query: str) -> Path:
    write(root, "model.tmdl", "model Model\n\tculture: en-GB\n")
    write(root, "database.tmdl", "database Loaded\n\tcompatibilityLevel: 1567\n")
    write(
        root,
        "tables/Batch.tmdl",
        "table Batch\n\n\tcolumn Yield\n\t\tdataType: int64\n\n"
        "\tmeasure Total = SUM(Batch[Yield])\n\n"
        "\tpartition Batch = m\n\t\tmode: import\n\t\tsource = ```\n"
        + "".join(f"\t\t\t{line}\n" for line in query.splitlines())
        + "\t\t\t```\n",
    )
    return root


def test_editing_a_load_query_is_drift(tmp_path: Path) -> None:
    """This is what the table fingerprint was widened for. It used to cover the
    table's name alone, so a query could be rewritten to load half the rows and
    every fingerprint in the model would stay identical."""
    before = SemanticGraph(
        TmdlAdapter().extract(str(build(tmp_path / "v1", "let A = Csv.Document(File.Contents(\"b.csv\")) in A")))
    )
    after = SemanticGraph(
        TmdlAdapter().extract(
            str(build(tmp_path / "v2",
                      "let A = Csv.Document(File.Contents(\"b.csv\")),\n"
                      "    B = Table.SelectRows(A, each [Status] = \"Released\")\n"
                      "in B"))
        )
    )
    report = compare(snap.take(before), snap.take(after), after_graph=after)

    changed = [c for c in report.changes if c.kind.value == "changed"]
    assert [c.object_kind for c in changed] == ["table"]
    # The measure over it is untouched, which is exactly the blind spot.
    assert before.model.measures[0].fingerprint == after.model.measures[0].fingerprint
    # And the detail shows the query, not the table's name twice over.
    assert "Table.SelectRows" in changed[0].after.detail


def test_reindenting_a_load_query_is_not_drift(tmp_path: Path) -> None:
    before = SemanticGraph(
        TmdlAdapter().extract(str(build(tmp_path / "v1", 'let A = Csv.Document(File.Contents("b.csv")) in A')))
    )
    after = SemanticGraph(
        TmdlAdapter().extract(
            str(build(tmp_path / "v2",
                      'let\n    A = Csv.Document( File.Contents("b.csv") )\nin\n    A'))
        )
    )
    report = compare(snap.take(before), snap.take(after), after_graph=after)
    assert not report.has_drift


def test_a_filtered_table_says_it_is_not_its_source(tmp_path: Path) -> None:
    root = build(
        tmp_path,
        'let A = Csv.Document(File.Contents("b.csv")),\n'
        '    B = Table.SelectRows(A, each [Status] = "Released")\n'
        "in B",
    )
    derived = RequirementDeriver(SemanticGraph(TmdlAdapter().extract(str(root)))).derive()
    statement = next(r.statement for r in derived if r.category == "Data acquisition")

    assert "filters rows" in statement
    assert "not a row-for-row copy" in statement


def test_an_unfiltered_table_does_not_carry_the_warning(tmp_path: Path) -> None:
    root = build(tmp_path, 'let A = Csv.Document(File.Contents("b.csv")) in A')
    derived = RequirementDeriver(SemanticGraph(TmdlAdapter().extract(str(root)))).derive()
    statement = next(r.statement for r in derived if r.category == "Data acquisition")
    assert "not a row-for-row copy" not in statement


def test_the_pbix_adapter_holds_the_same_rule_as_tmdl() -> None:
    """A guarantee that depends on which file format a model was saved in is
    not a guarantee. .pbix extracts M for most tables, and for a while
    fingerprinted only their names -- so the blind spot closed above stayed
    wide open for every .pbix model."""
    from concordance.adapters.pbix import PbixAdapter
    from concordance.fingerprint import fingerprint_text

    source = Path("data/models/Sales_Returns_Sample.pbix")
    if not source.exists():
        pytest.skip(f"model not present: {source}")

    model = PbixAdapter().extract(str(source))
    with_query = [t for t in model.tables if t.power_query]
    assert with_query, "the fixture is meant to carry Power Query"
    for table in with_query:
        assert table.fingerprint != fingerprint_text(table.name), (
            f"{table.name} carries M that its fingerprint does not cover"
        )
    # And a table with no query is still identified by its name alone.
    for table in model.tables:
        if not table.power_query:
            assert table.fingerprint == fingerprint_text(table.name)

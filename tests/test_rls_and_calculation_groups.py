"""Row-level security and calculation groups, end to end.

Both were reported as coverage gaps for most of this project's life, and both
were the *right* things to report: each one can make an otherwise correct
document wrong in a way no measure's own fingerprint reveals.

  * An RLS filter decides which rows a reader's figures are computed over.
    Edit it and every number that reader sees changes while every measure in
    the model keeps the fingerprint it had.
  * A calculation item substitutes its own DAX around whatever measure is on
    the report. Edit it and the same thing happens.

So the tests that matter are not "can we parse this" but "does a change to one
of these register as drift, and does it put the right requirement in question".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.drift import snapshot as snap
from concordance.drift.compare import compare
from concordance.generate.requirements import Kind, RequirementDeriver
from concordance.graph.csg import SemanticGraph


def write(root: Path, relative: str, text: str) -> None:
    path = root / "definition" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build(tmp_path: Path, *, filter_expression: str, ytd: str) -> Path:
    """A small but genuinely valid model carrying both constructs."""
    root = tmp_path / "Secured.SemanticModel"
    write(root, "model.tmdl", "model Model\n\tculture: en-GB\n")
    write(root, "database.tmdl", "database Secured\n\tcompatibilityLevel: 1567\n")
    write(
        root,
        "tables/Sales.tmdl",
        "table Sales\n\n\tcolumn Amount\n\t\tdataType: double\n\n"
        "\tcolumn Region\n\t\tdataType: string\n\n"
        "\tmeasure Revenue = SUM(Sales[Amount])\n",
    )
    write(
        root,
        "tables/Lens.tmdl",
        "table Lens\n\n\tcalculationGroup\n\t\tprecedence: 10\n\n"
        f"\t\tcalculationItem YTD = {ytd}\n\n"
        "\tcolumn Name\n\t\tdataType: string\n",
    )
    write(
        root,
        "roles.tmdl",
        "role Regional\n\tmodelPermission: read\n\n"
        f"\ttablePermission Sales = {filter_expression}\n",
    )
    return root


@pytest.fixture
def model_root(tmp_path: Path) -> Path:
    return build(
        tmp_path,
        filter_expression='Sales[Region] = "West"',
        ytd="TOTALYTD(SELECTEDMEASURE(), 'Calendar'[Date])",
    )


# -- extraction ---------------------------------------------------------------

def test_a_role_filter_is_read_as_dax_and_fingerprinted(model_root: Path) -> None:
    model = TmdlAdapter().extract(str(model_root))
    role = model.roles[0]
    assert role.name == "Regional"
    assert role.model_permission == "read"
    assert role.permissions[0].table == "Sales"
    assert role.permissions[0].expression == 'Sales[Region] = "West"'
    assert len(role.permissions[0].fingerprint) == 64


def test_a_reformatted_role_filter_is_not_a_change(tmp_path: Path) -> None:
    """The same guarantee measures already have, applied to RLS: whitespace and
    casing are not logic, so reformatting a filter must not read as drift."""
    plain = TmdlAdapter().extract(
        str(build(tmp_path / "a", filter_expression='Sales[Region]="West"', ytd="SELECTEDMEASURE()"))
    )
    spaced = TmdlAdapter().extract(
        str(build(tmp_path / "b", filter_expression='Sales[Region]   =  "West"', ytd="SELECTEDMEASURE()"))
    )
    assert (
        plain.roles[0].permissions[0].fingerprint
        == spaced.roles[0].permissions[0].fingerprint
    )


def test_each_calculation_item_carries_its_own_fingerprint(model_root: Path) -> None:
    """Group-level hashing alone would say "the group changed" without saying
    which rewrite moved, which is the only part a reviewer can act on."""
    model = TmdlAdapter().extract(str(model_root))
    group = model.calculation_groups[0]
    assert group.table == "Lens"
    assert group.precedence == 10
    assert group.items[0].name == "YTD"
    assert len(group.items[0].fingerprint) == 64


# -- the graph ----------------------------------------------------------------

def test_a_role_is_edged_to_every_table_it_restricts(model_root: Path) -> None:
    graph = SemanticGraph(TmdlAdapter().extract(str(model_root)))
    assert "role:Regional" in graph.graph
    assert graph.graph.has_edge("role:Regional", "table:Sales")


def test_a_role_filtering_a_table_that_does_not_exist_is_recorded_not_dropped(
    tmp_path: Path,
) -> None:
    root = build(tmp_path, filter_expression='Sales[Region] = "West"', ytd="SELECTEDMEASURE()")
    write(
        root,
        "roles.tmdl",
        "role Ghost\n\tmodelPermission: read\n\n"
        "\ttablePermission Missing = [X] = 1\n",
    )
    graph = SemanticGraph(TmdlAdapter().extract(str(root)))
    assert any(u.target == "table:Missing" for u in graph.unresolved)


# -- drift: the reason any of this is read at all -----------------------------

def test_editing_a_role_filter_is_drift_even_though_no_measure_moved(
    tmp_path: Path,
) -> None:
    before = SemanticGraph(
        TmdlAdapter().extract(
            str(build(tmp_path / "v1", filter_expression='Sales[Region] = "West"',
                      ytd="SELECTEDMEASURE()"))
        )
    )
    after = SemanticGraph(
        TmdlAdapter().extract(
            str(build(tmp_path / "v2", filter_expression='Sales[Region] = "East"',
                      ytd="SELECTEDMEASURE()"))
        )
    )
    report = compare(snap.take(before), snap.take(after), after_graph=after)

    changed = [c for c in report.changes if c.kind.value == "changed"]
    assert [c.object_kind for c in changed] == ["role"]
    # The measure is untouched, which is precisely why this had to be read.
    assert before.model.measures[0].fingerprint == after.model.measures[0].fingerprint
    assert report.affected, "a changed filter must put its requirement in question"


def test_editing_a_calculation_item_puts_its_requirement_in_question(
    tmp_path: Path,
) -> None:
    before = SemanticGraph(
        TmdlAdapter().extract(
            str(build(tmp_path / "v1", filter_expression="[X] = 1",
                      ytd="TOTALYTD(SELECTEDMEASURE(), 'Calendar'[Date])"))
        )
    )
    after = SemanticGraph(
        TmdlAdapter().extract(
            str(build(tmp_path / "v2", filter_expression="[X] = 1",
                      ytd="TOTALQTD(SELECTEDMEASURE(), 'Calendar'[Date])"))
        )
    )
    report = compare(snap.take(before), snap.take(after), after_graph=after)

    changed = [c for c in report.changes if c.kind.value == "changed"]
    assert [c.object_kind for c in changed] == ["calculation_item"]
    assert any(a.needs_revalidation for a in report.affected)


# -- requirements -------------------------------------------------------------

def test_both_documents_state_the_restriction(model_root: Path) -> None:
    derived = RequirementDeriver(SemanticGraph(TmdlAdapter().extract(str(model_root)))).derive()

    business = [r for r in derived if r.category == "Access and visibility"]
    assert len(business) == 1
    assert "Regional" in business[0].statement
    assert "Sales" in business[0].statement

    functional = [r for r in derived if r.category == "Row-level security"]
    assert len(functional) == 1
    assert functional[0].kind is Kind.FUNCTIONAL
    # The filter itself has to be in the evidence: it is the thing an
    # implementer reproduces, and the thing whose edit changes the numbers.
    assert functional[0].evidence[0].detail == 'Sales[Region] = "West"'


def test_a_role_with_no_filters_is_not_described_as_restricting_rows(
    tmp_path: Path,
) -> None:
    """A role can exist purely to grant model-wide access. Saying it "shall see
    only the rows the filter admits" when there is no filter would be false."""
    root = build(tmp_path, filter_expression="[X] = 1", ytd="SELECTEDMEASURE()")
    write(root, "roles.tmdl", "role Everyone\n\tmodelPermission: read\n")

    derived = RequirementDeriver(SemanticGraph(TmdlAdapter().extract(str(root)))).derive()
    statement = next(
        r.statement for r in derived if r.category == "Access and visibility"
    )
    assert "without restricting" in statement
    assert "only the rows" not in statement


def test_calculation_items_are_documented_as_options_not_as_metrics(
    model_root: Path,
) -> None:
    derived = RequirementDeriver(SemanticGraph(TmdlAdapter().extract(str(model_root)))).derive()
    functional = [r for r in derived if r.category == "Calculation groups"]
    assert len(functional) == 1
    assert "YTD" in functional[0].statement
    assert functional[0].evidence[0].detail.startswith("TOTALYTD")

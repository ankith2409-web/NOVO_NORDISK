"""The last three constructs that changed what a document means.

Each was reported as a coverage gap and is now read:

  * A **KPI** is not the measure it decorates. The measure says what the
    number is; the KPI says what the business calls good. Moving a status
    threshold from 80% to 90% turns amber figures red without touching a line
    of the measure's DAX -- so the thresholds are fingerprinted separately.
  * **Object-level security** hides a field outright rather than filtering its
    rows. A document describing a column half its audience cannot see is
    describing a model none of them use.
  * A **perspective** is a scope statement. The Finance view of a model is a
    different product from the Operations view, and a document covering the
    union describes something nobody is given.

The .pbix side is exercised against frames shaped by PBIXRay's own published
SQL, for the reason set out in `test_pbix_roles_and_groups.py`.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from concordance.adapters.pbix import (
    build_kpis,
    build_object_permissions,
    build_perspectives,
)
from concordance.adapters.tmdl import TmdlAdapter
from concordance.drift import snapshot as snap
from concordance.drift.compare import compare
from concordance.generate.requirements import RequirementDeriver
from concordance.graph.csg import SemanticGraph

MODEL = Path("data/models/DiabetesCare.SemanticModel")


def _model():
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return TmdlAdapter().extract(str(MODEL))


def write(root: Path, relative: str, text: str) -> None:
    path = root / "definition" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build(root: Path, *, status: str) -> Path:
    """A model whose one measure carries a KPI, so a threshold can be moved."""
    write(root, "model.tmdl", "model Model\n\tculture: en-GB\n")
    write(root, "database.tmdl", "database Judged\n\tcompatibilityLevel: 1567\n")
    write(
        root,
        "tables/Sales.tmdl",
        "table Sales\n\n\tcolumn Amount\n\t\tdataType: double\n\n"
        "\tmeasure Revenue = SUM(Sales[Amount])\n\n"
        "\t\tkpi\n"
        "\t\t\ttargetExpression = 1000000\n"
        f"\t\t\tstatusExpression = {status}\n",
    )
    return root


# -- TMDL, against the real shipped model -------------------------------------

def test_a_kpi_is_read_with_its_target_and_thresholds() -> None:
    model = _model()
    assert len(model.kpis) == 1
    kpi = model.kpis[0]
    assert kpi.qualified_name == "Diabetes Metrics[Diabetes Prevalence]"
    assert kpi.target_expression == "[Overall Diabetes Prevalence]"
    assert "IF(Ratio <= 1" in kpi.status_expression
    assert kpi.target_description == "Cohort-wide prevalence"


def test_object_level_security_is_distinguished_from_row_filtering() -> None:
    """Both restrict the Cohort Clinician, and they are not the same thing:
    one shows fewer rows, the other hides that a field exists at all."""
    model = _model()

    hidden = [p for p in model.object_permissions if p.hides]
    assert len(hidden) == 1
    assert hidden[0].role == "Cohort Clinician"
    assert hidden[0].target == "Patient[DiabetesPedigreeFunction]"

    # The same role still carries its row filter, recorded separately.
    role = next(r for r in model.roles if r.name == "Cohort Clinician")
    assert role.restricted_tables == ("Patient",)


def test_a_perspective_carries_its_membership_not_just_its_name() -> None:
    model = _model()
    perspective = model.perspectives[0]
    assert perspective.name == "Population Health"
    assert perspective.tables == ("Diabetes Metrics", "Patient")
    measures = {m.name for m in perspective.members if m.object_kind == "Measure"}
    assert "Diabetes Prevalence" in measures
    # A perspective that exposes a subset must not claim the whole model.
    assert "Average BMI" not in measures


def test_the_shipped_model_reports_nothing_unread() -> None:
    assert _model().coverage_gaps == []


# -- the reason a KPI is its own node -----------------------------------------

def test_moving_a_threshold_is_drift_though_the_measure_is_untouched(
    tmp_path: Path,
) -> None:
    before = SemanticGraph(
        TmdlAdapter().extract(str(build(tmp_path / "v1", status="IF([Revenue] > 800000, 1, -1)")))
    )
    after = SemanticGraph(
        TmdlAdapter().extract(str(build(tmp_path / "v2", status="IF([Revenue] > 900000, 1, -1)")))
    )
    report = compare(snap.take(before), snap.take(after), after_graph=after)

    changed = [c for c in report.changes if c.kind.value == "changed"]
    assert [c.object_kind for c in changed] == ["kpi"]
    assert before.model.measures[0].fingerprint == after.model.measures[0].fingerprint
    assert report.affected, "the threshold requirement must be put in question"


def test_a_kpi_is_edged_to_the_measure_it_decorates(tmp_path: Path) -> None:
    graph = SemanticGraph(
        TmdlAdapter().extract(str(build(tmp_path, status="IF([Revenue] > 1, 1, -1)")))
    )
    assert graph.graph.has_edge("kpi:Sales[Revenue]", "measure:Sales[Revenue]")


def test_the_served_graph_carries_each_threshold_separately() -> None:
    """The three rules split, as well as joined.

    The joined ``expression`` is what the fingerprint covers and what drift
    reports as this object's detail, so it stays. The split fields exist so that
    anything displaying a KPI -- the Model tab does -- reads them off the graph
    instead of taking that joined string back apart, which would be a second
    parse of text this code just finished assembling and would eventually
    disagree with it.
    """
    node = next(
        n
        for n in SemanticGraph(_model()).to_dict()["nodes"]
        if n["kind"] == "kpi"
    )
    assert node["target_expression"] == "[Overall Diabetes Prevalence]"
    assert "IF(Ratio <= 1" in node["status_expression"]
    assert node["target_description"] == "Cohort-wide prevalence"
    # An unset rule is present and empty rather than absent: the page says
    # "no trend rule" only because it can tell the difference between a rule
    # that is blank and a field it was never given.
    assert node["trend_expression"] == ""
    # And the joined form the fingerprint is taken over survives untouched.
    assert node["expression"].startswith("target: [Overall Diabetes Prevalence]")


def test_splitting_the_thresholds_did_not_move_any_fingerprint(tmp_path: Path) -> None:
    """Adding fields to the node must not change what drift compares.

    A snapshot reads ``expression`` for an object's detail and ignores every
    other attribute. If that stopped being true, every KPI in every model would
    report as changed the first time this ran against an older snapshot -- drift
    crying wolf on a display change, which is the one thing it must never do.
    """
    graph = SemanticGraph(
        TmdlAdapter().extract(str(build(tmp_path, status="IF([Revenue] > 1, 1, -1)")))
    )
    record = snap.take(graph).objects["kpi:Sales[Revenue]"]
    assert record.detail.startswith("target:")
    assert "target_expression" not in record.detail


# -- requirements --------------------------------------------------------------

def test_each_construct_reaches_the_document() -> None:
    derived = RequirementDeriver(SemanticGraph(_model())).derive()
    by_category: dict[str, list[str]] = {}
    for requirement in derived:
        by_category.setdefault(requirement.category, []).append(requirement.statement)

    kpi = next(s for s in by_category["Metrics and KPIs"] if "key performance" in s)
    assert "Cohort-wide prevalence" in kpi

    access = by_category["Access and visibility"]
    assert any("shall not have access to" in s for s in access)
    assert any("Population Health" in s and "shall expose" in s for s in access)

    threshold = by_category["KPI thresholds"]
    assert len(threshold) == 1


def test_a_kpi_with_no_status_expression_gets_no_threshold_requirement(
    tmp_path: Path,
) -> None:
    """Asserting "the status shall be computed by the expression recorded here"
    with nothing recorded is the confidently-wrong documentation this project
    exists to prevent."""
    root = tmp_path / "NoStatus.SemanticModel"
    write(root, "model.tmdl", "model Model\n\tculture: en-GB\n")
    write(root, "database.tmdl", "database NoStatus\n\tcompatibilityLevel: 1567\n")
    write(
        root,
        "tables/Sales.tmdl",
        "table Sales\n\n\tcolumn Amount\n\t\tdataType: double\n\n"
        "\tmeasure Revenue = SUM(Sales[Amount])\n\n"
        "\t\tkpi\n\t\t\ttargetExpression = 1000000\n",
    )
    derived = RequirementDeriver(SemanticGraph(TmdlAdapter().extract(str(root)))).derive()
    assert not [r for r in derived if r.category == "KPI thresholds"]
    # The business-level statement is still made: the target is real.
    assert [r for r in derived if "key performance" in r.statement]


# -- .pbix, against frames shaped by PBIXRay's published SQL --------------------

def test_pbix_kpis_are_read_from_the_documented_columns() -> None:
    kpis = build_kpis(
        pd.DataFrame(
            [
                {
                    "MeasureName": "Revenue", "TableName": "Sales",
                    "Description": None, "TargetDescription": "Annual plan",
                    "TargetExpression": "10000000", "StatusDescription": None,
                    "StatusExpression": "IF([Revenue] > 9000000, 1, -1)",
                    "TrendExpression": "",
                }
            ]
        )
    )
    assert len(kpis) == 1
    assert kpis[0].qualified_name == "Sales[Revenue]"
    assert kpis[0].target_description == "Annual plan"
    assert kpis[0].fingerprint


def test_pbix_ols_drops_the_default_permission_rows() -> None:
    """`Default` means "no OLS restriction" -- it is the ordinary row-level
    case, already reported through `rls`. Keeping those rows would list every
    RLS role as though it hid something."""
    permissions = build_object_permissions(
        pd.DataFrame(
            [
                {"RoleName": "R", "TableName": "Sales", "ColumnName": "Cost",
                 "Scope": "Column", "Permission": "None"},
                {"RoleName": "R", "TableName": "Sales", "ColumnName": None,
                 "Scope": "Table", "Permission": "Default"},
            ]
        )
    )
    assert len(permissions) == 1
    assert permissions[0].target == "Sales[Cost]"
    assert permissions[0].hides is True


def test_pbix_perspectives_roll_rows_up_into_one_entry() -> None:
    perspectives = build_perspectives(
        pd.DataFrame(
            [
                {"PerspectiveName": "Finance", "ObjectType": "Table",
                 "TableName": "Sales", "ObjectName": "Sales", "IncludeAll": 1},
                {"PerspectiveName": "Finance", "ObjectType": "Measure",
                 "TableName": "Sales", "ObjectName": "Revenue", "IncludeAll": None},
            ]
        )
    )
    assert len(perspectives) == 1
    assert perspectives[0].name == "Finance"
    assert perspectives[0].tables == ("Sales",)
    assert len(perspectives[0].members) == 2


def test_absent_frames_yield_nothing() -> None:
    assert build_kpis(None) == []
    assert build_object_permissions(None) == []
    assert build_perspectives(None) == []


# -- column variations and role membership ------------------------------------

def test_a_variation_resolves_to_the_hierarchy_it_navigates() -> None:
    from concordance.adapters.pbix import build_variations

    variations = build_variations(
        pd.DataFrame(
            [{"TableName": "Calendar", "ColumnName": "Date", "Name": "Variation",
              "DefaultHierarchyID": 1524, "IsDefault": 1}]
        ),
        pd.DataFrame([{"ID": 1524, "TableName": "Calendar", "Name": "Date Hierarchy"}]),
    )
    assert len(variations) == 1
    assert variations[0].qualified_name == "Calendar[Date]"
    assert variations[0].default_hierarchy == "Calendar[Date Hierarchy]"
    assert variations[0].is_default is True


def test_an_unresolvable_hierarchy_is_left_absent_not_shown_as_an_id() -> None:
    """A numeric key in a requirements document is noise nobody can act on."""
    from concordance.adapters.pbix import build_variations

    variations = build_variations(
        pd.DataFrame(
            [{"TableName": "T", "ColumnName": "C", "Name": "V",
              "DefaultHierarchyID": 999, "IsDefault": 0}]
        ),
        pd.DataFrame([]),
    )
    assert variations[0].default_hierarchy is None


def test_an_auto_date_variation_is_described_not_reproduced() -> None:
    """Nearly every real variation points at a generated LocalDateTable_<guid>
    hierarchy. Naming it in the statement would put a GUID in a business
    document and describe a table this tool hides everywhere else."""
    from concordance.adapters.pbix import PbixAdapter

    source = Path("data/models/AdventureWorks_Sales.pbix")
    if not source.exists():
        pytest.skip(f"model not present: {source}")

    derived = RequirementDeriver(
        SemanticGraph(PbixAdapter().extract(str(source)))
    ).derive()
    drills = [r for r in derived if r.category == "Navigation and drill paths"]
    assert drills, "AdventureWorks carries three variations"

    for requirement in drills:
        assert "automatic date hierarchy" in requirement.statement
        assert "LocalDateTable_" not in requirement.statement
        # The evidence still records the real target: the statement is for
        # reading, the evidence is for checking.
        assert "LocalDateTable_" in requirement.evidence[0].detail


def test_membership_reaches_roles_the_rls_frame_cannot_show() -> None:
    """A role filtering no table has no row in `rls` at all -- that query joins
    TablePermission. If it has members it has a row here, which is the one
    place such a role becomes visible."""
    from concordance.adapters.pbix import _with_members, build_roles

    roles = _with_members(
        build_roles(
            pd.DataFrame(
                [{"TableName": "Sales", "RoleName": "EMEA", "RoleDescription": None,
                  "FilterExpression": "[R] = 1", "State": "Ready",
                  "MetadataPermission": None}],
                columns=["TableName", "RoleName", "RoleDescription",
                         "FilterExpression", "State", "MetadataPermission"],
            )
        ),
        {"EMEA": ("anna@example.com",), "Auditors": ("bo@example.com",)},
    )

    by_name = {r.name: r for r in roles}
    assert by_name["EMEA"].members == ("anna@example.com",)
    # Never seen by `rls`, recovered here rather than dropped.
    assert by_name["Auditors"].members == ("bo@example.com",)
    assert by_name["Auditors"].permissions == ()


def test_a_tmdl_variation_is_read_from_the_column_it_sits_on(tmp_path: Path) -> None:
    root = tmp_path / "V.SemanticModel"
    write(root, "model.tmdl", "model Model\n\tculture: en-GB\n")
    write(root, "database.tmdl", "database V\n\tcompatibilityLevel: 1567\n")
    write(
        root,
        "tables/Calendar.tmdl",
        "table Calendar\n\n\tcolumn Date\n\t\tdataType: dateTime\n\n"
        "\t\tvariation Variation\n\t\t\tisDefault\n"
        "\t\t\tdefaultHierarchy: Calendar.'Date Hierarchy'\n",
    )
    model = TmdlAdapter().extract(str(root))

    assert len(model.variations) == 1
    assert model.variations[0].qualified_name == "Calendar[Date]"
    assert model.variations[0].is_default is True
    assert model.coverage_gaps == [], "a variation that is read is not a gap"

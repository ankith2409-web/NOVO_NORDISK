"""What a TMDL model contains that this adapter does not read.

The bug these tests exist to prevent was a sentence: ``inspect`` printed
"Coverage: no unextracted model features detected" for every TMDL model,
because the TMDL adapter never populated ``coverage_gaps`` at all. That is a
claim about a check that was never run, and it is the worst possible bug for
this project specifically -- "we tell you what we did not read" is the promise
everything else rests on, and it was being asserted by a code path that could
not have known.

The demo models happen to contain none of these constructs, so the sentence was
accidentally true for them. Accidentally true is not the same as checked, which
is why every test below builds a model that *does* contain something and proves
it is reported.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter


def write(root: Path, relative: str, text: str) -> None:
    path = root / "definition" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def model_root(tmp_path: Path) -> Path:
    """A minimal but genuinely valid TMDL model, to add constructs onto."""
    root = tmp_path / "Fixture.SemanticModel"
    write(
        root,
        "tables/Patient.tmdl",
        "table Patient\n\n\tcolumn PatientID\n\t\tdataType: int64\n\n"
        "\tmeasure 'Patient Count' = COUNTROWS(Patient)\n",
    )
    return root


def gaps(root: Path) -> dict[str, int]:
    model = TmdlAdapter().extract(str(root))
    return {gap.feature: gap.count for gap in model.coverage_gaps}


# -- the constructs that change what the documentation means ------------------

def test_row_level_security_stops_being_a_gap_once_it_is_read(
    model_root: Path,
) -> None:
    """The invariant has to hold in both directions.

    These two constructs used to be reported here, correctly, as things the
    adapter did not read. They are read now -- so the gap list must no longer
    claim otherwise. That it does so without anyone editing a list is the
    point of deriving gaps from the files: extraction growing shrinks the
    disclaimer automatically, and the two can never drift apart.
    """
    write(
        model_root,
        "roles.tmdl",
        "role SiteInvestigator\n\tmodelPermission: read\n\n"
        "\ttablePermission Patient = [SiteID] = USERNAME()\n",
    )
    assert "role" not in gaps(model_root)
    assert "tablePermission" not in gaps(model_root)

    model = TmdlAdapter().extract(str(model_root))
    assert [r.name for r in model.roles] == ["SiteInvestigator"]
    assert model.roles[0].permissions[0].expression == "[SiteID] = USERNAME()"


def test_calculation_groups_stop_being_a_gap_once_they_are_read(
    model_root: Path,
) -> None:
    write(
        model_root,
        "calculationGroups.tmdl",
        "table 'Time Intelligence'\n\n\tcalculationGroup\n\n"
        "\t\tcalculationItem 'Year to Date' = "
        "CALCULATE(SELECTEDMEASURE(), DATESYTD('Calendar'[Date]))\n\n"
        "\t\tcalculationItem 'Prior Year' = "
        "CALCULATE(SELECTEDMEASURE(), SAMEPERIODLASTYEAR('Calendar'[Date]))\n",
    )
    found = gaps(model_root)
    assert "calculationGroup" not in found
    assert "calculationItem" not in found

    model = TmdlAdapter().extract(str(model_root))
    assert [i.name for i in model.calculation_groups[0].items] == [
        "Year to Date", "Prior Year",
    ]


def test_perspectives_stop_being_a_gap_once_they_are_read(model_root: Path) -> None:
    write(
        model_root,
        "perspectives.tmdl",
        "perspective Clinical\n\n\tperspectiveTable Patient\n\n"
        "\t\tperspectiveMeasure 'Patient Count'\n",
    )
    assert "perspective" not in gaps(model_root)

    model = TmdlAdapter().extract(str(model_root))
    assert [p.name for p in model.perspectives] == ["Clinical"]
    # Membership, not just the name: a perspective whose contents are unknown
    # says nothing about what its audience is actually shown.
    assert model.perspectives[0].tables == ("Patient",)
    assert {m.name for m in model.perspectives[0].members} == {"Patient", "Patient Count"}


def test_translations_are_reported(model_root: Path) -> None:
    write(model_root, "cultures/fr-FR.tmdl", "culture fr-FR\n\tlinguisticMetadata: {}\n")
    assert gaps(model_root)["culture"] == 1


# -- the property that makes this hold for constructs nobody listed -----------

def test_a_construct_this_adapter_has_never_heard_of_is_still_counted(
    model_root: Path,
) -> None:
    """The reason the gap list is derived from the files rather than declared.

    A hand-maintained list of known-skipped features only stays true until
    someone adds a construct nobody thought of -- and Power BI adds them. Here
    the parser meets a declaration with no entry in the reason table at all,
    and it still has to be counted rather than silently dropped.
    """
    # Deliberately not a real TMDL keyword. `tablePermission` used to stand in
    # here and no longer can -- it is extracted now -- which is exactly the
    # churn this test exists to survive.
    write(model_root, "roles.tmdl", "role Auditor\n\tqueryScopeFilter Patient = [X] = 1\n")
    found = gaps(model_root)
    assert "queryScopeFilter" in found
    reason = next(
        g.reason
        for g in TmdlAdapter().extract(str(model_root)).coverage_gaps
        if g.feature == "queryScopeFilter"
    )
    assert reason, "an unknown construct still needs some stated reason"


# -- and the other half: silence has to mean something ------------------------

def test_a_model_with_nothing_unread_reports_nothing(model_root: Path) -> None:
    """The claim only means anything if it can distinguish the two cases.

    A checker that always finds something is as useless as one that never does,
    and the second is the bug that was actually shipped.
    """
    assert gaps(model_root) == {}


def test_what_is_extracted_is_never_reported_as_a_gap(model_root: Path) -> None:
    """Tables, columns, measures and hierarchies all reach the graph, so listing
    them as unread would bury the real findings under noise present in every
    model."""
    write(
        model_root,
        "tables/Visit.tmdl",
        "table Visit\n\n\tcolumn VisitID\n\t\tdataType: int64\n\n"
        "\tcolumn VisitDate\n\t\tdataType: dateTime\n\n"
        "\thierarchy Schedule\n\n\t\tlevel Year\n\t\t\tcolumn: VisitDate\n\n"
        "\tpartition Visit = m\n\t\tmode: import\n\t\tsource = let Source = 1 in Source\n",
    )
    found = gaps(model_root)
    for extracted in ("table", "column", "measure", "hierarchy", "level", "partition"):
        assert extracted not in found


def test_the_real_demo_models_are_reported_honestly() -> None:
    """Not an assertion that they are clean -- an assertion that whatever is
    reported for them came from reading their files. These models genuinely
    contain no roles or calculation groups, which is why the broken claim went
    unnoticed for so long."""
    root = Path("data/models/QualityControl.SemanticModel")
    if not root.exists():
        pytest.skip(f"model not present: {root}")
    model = TmdlAdapter().extract(str(root))
    # Whatever is found, every entry has to carry a real count and a reason.
    for gap in model.coverage_gaps:
        assert gap.count > 0
        assert gap.reason.strip()

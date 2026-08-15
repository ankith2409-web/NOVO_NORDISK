"""The DiabetesCare fixture: a sample model built on real, public data.

Every other sample model is synthetic, authored to mirror real structure.
This one is different -- it is built directly on the Pima Indians Diabetes
Dataset (768 real patient records, see data/samples/README.md) -- and that
distinction is worth its own regression: a model this project ships must
extract cleanly and generate a document with the same guarantees as the
synthetic fixtures, not a lesser demo carve-out.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.generate.requirements import Confidence, Kind, RequirementDeriver
from concordance.graph.csg import SemanticGraph

MODEL = Path("data/models/DiabetesCare.SemanticModel")
DATA = Path("data/samples/diabetes_patients.csv")


def _available() -> Path:
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return MODEL


def test_the_real_dataset_is_present_and_the_shape_the_model_expects() -> None:
    if not DATA.exists():
        pytest.skip(f"dataset not present: {DATA}")
    lines = DATA.read_text(encoding="utf-8").splitlines()
    # 768 patients plus the header row this project added for readability.
    assert len(lines) == 769
    assert lines[0].split(",") == [
        "Pregnancies", "Glucose", "BloodPressure", "SkinThickness", "Insulin",
        "BMI", "DiabetesPedigreeFunction", "Age", "Outcome",
    ]


def test_the_model_extracts_with_no_unread_constructs() -> None:
    graph = SemanticGraph(TmdlAdapter().extract(str(_available())))
    assert {t.name for t in graph.model.user_tables()} == {
        "Patient", "Diabetes Metrics", "Risk Lens",
    }
    assert len(graph.model.measures) == 12
    assert graph.model.coverage_gaps == []


def test_the_security_roles_and_risk_lens_are_read_not_merely_noticed() -> None:
    """This model carries the two constructs that used to be coverage gaps, so
    it is where their extraction is exercised against a real file rather than
    a fixture built inside a test."""
    model = TmdlAdapter().extract(str(_available()))

    roles = {r.name: r for r in model.roles}
    assert set(roles) == {"Cohort Clinician", "Population Analyst"}
    # One restricts rows, one deliberately does not -- both must be documented.
    assert roles["Cohort Clinician"].restricted_tables == ("Patient",)
    assert roles["Population Analyst"].permissions == ()
    assert roles["Cohort Clinician"].permissions[0].fingerprint

    group = model.calculation_groups[0]
    assert group.table == "Risk Lens"
    assert [i.name for i in group.items] == [
        "All Patients", "Elevated Glucose Only", "Share Of Population",
    ]
    assert all(i.fingerprint for i in group.items)


def test_every_measure_fingerprints_and_every_requirement_derives_high_confidence() -> None:
    """Real DAX, same as any other model: fingerprinted, and not flagged for
    review just because the data behind it is real rather than synthetic."""
    graph = SemanticGraph(TmdlAdapter().extract(str(_available())))
    for measure in graph.model.measures:
        assert measure.fingerprint

    derived = RequirementDeriver(graph).derive()
    assert derived
    assert all(r.confidence is Confidence.HIGH for r in derived)
    assert any(r.kind is Kind.BUSINESS for r in derived)
    assert any(r.kind is Kind.FUNCTIONAL for r in derived)


def test_the_ratio_and_conditional_patterns_are_recognised() -> None:
    """Confirms the model actually exercises DIVIDE and SWITCH, not just AVERAGE."""
    graph = SemanticGraph(TmdlAdapter().extract(str(_available())))
    by_name = {m.name: m.expression for m in graph.model.measures}
    assert "DIVIDE" in by_name["Diabetes Prevalence"]
    assert "ALL(" in by_name["Overall Diabetes Prevalence"]

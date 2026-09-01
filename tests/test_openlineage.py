"""Emitting lineage in the format the rest of the ecosystem reads.

Checked against the OpenLineage spec's own requirements rather than against
what looked reasonable: a DatasetEvent's required fields, a facet's required
`_producer` and `_schemaURL`, and the ColumnLineageDatasetFacet's required
`fields` / `inputFields` / `namespace` / `name` / `field` shape.

The claim being made is narrow and the tests keep it that way. This emits
*static* lineage -- what the model defines -- and never a RunEvent, because a
semantic model is a standing description rather than an execution, and a run id
we invented would be a claim about something that never ran.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.tmdl import TmdlAdapter
from concordance.generate import openlineage as ol
from concordance.graph.csg import SemanticGraph

MODEL = Path("data/models/QualityControl.SemanticModel")


@pytest.fixture(scope="module")
def graph() -> SemanticGraph:
    if not MODEL.exists():
        pytest.skip(f"model not present: {MODEL}")
    return SemanticGraph(TmdlAdapter().extract(str(MODEL)))


@pytest.fixture(scope="module")
def emitted(graph):
    return ol.emit(graph)


def test_every_event_carries_what_a_dataset_event_requires(emitted) -> None:
    """eventTime, producer, schemaURL, dataset -- all four, per the spec."""
    assert emitted.events
    for event in emitted.events:
        for required in ("eventTime", "producer", "schemaURL", "dataset"):
            assert required in event, f"DatasetEvent missing {required}"
        assert event["dataset"]["namespace"]
        assert event["dataset"]["name"]


def test_no_event_claims_a_run_that_never_happened(emitted) -> None:
    """A DatasetEvent is defined as unable to carry a job or a run.

    The temptation is to emit a RunEvent, because that is the shape most
    OpenLineage integrations produce and the one most tools expect. It would
    also be a lie: nothing ran. Reading a file is not a job execution.
    """
    for event in emitted.events:
        assert "run" not in event
        assert "job" not in event


def test_every_facet_identifies_itself(emitted) -> None:
    """`_producer` and `_schemaURL` are required on every facet, so a consumer
    can tell what wrote it and against which version."""
    for event in emitted.events:
        for name, facet in event["dataset"]["facets"].items():
            assert facet.get("_producer"), f"{name} does not say what produced it"
            assert facet.get("_schemaURL"), f"{name} does not name its schema"


def test_column_lineage_has_the_shape_the_facet_defines(emitted) -> None:
    """fields -> inputFields -> {namespace, name, field}, all required."""
    seen = 0
    for event in emitted.events:
        lineage = event["dataset"]["facets"].get("columnLineage")
        if not lineage:
            continue
        assert isinstance(lineage["fields"], dict)
        for output, detail in lineage["fields"].items():
            assert detail["inputFields"], f"{output} claims lineage with no inputs"
            for source in detail["inputFields"]:
                for required in ("namespace", "name", "field"):
                    assert required in source, f"inputField missing {required}"
                for transformation in source.get("transformations", []):
                    assert transformation["type"] in ("DIRECT", "INDIRECT")
                seen += 1
    assert seen, "no column lineage was emitted at all"


def test_a_measure_resolves_to_the_columns_it_really_reads(emitted) -> None:
    """Through its measure references, not stopping at the first name.

    `OOS Rate` is defined over `[OOS Results]` and `[Tests Performed]`. Naming
    those as its lineage would be true and useless to a catalog, which wants
    the physical columns. It resolves through the same compiler that writes the
    measure's SQL, so the answer is the base column.
    """
    metrics = next(
        e for e in emitted.events if e["dataset"]["name"].endswith("QC Metrics")
    )
    fields = metrics["dataset"]["facets"]["columnLineage"]["fields"]
    reads = {
        (f["name"], f["field"]) for f in fields["OOS Rate"]["inputFields"]
    }
    assert ("TestResult", "ResultStatus") in reads
    assert not any(name == "QC Metrics" for name, _ in reads), (
        "lineage stopped at the measure it was defined over"
    )


def test_a_measure_whose_columns_are_not_fixed_is_left_out_and_reported(
    emitted,
) -> None:
    """Filter-context measures have no fixed set of columns, so they get no
    lineage -- and the omission is returned rather than silently dropped.

    A catalog fed a model with a quarter of its measures missing, and no note
    saying so, shows something simpler than the truth.
    """
    assert emitted.omitted
    names = {measure for measure, _ in emitted.omitted}
    # Still genuinely undecidable: RANKX ranks against a row set the report
    # chooses, so there is no fixed list of columns it reads.
    assert "Instrument Failure Rank" in names
    # And no longer omitted: a previous-period measure translates now, so its
    # columns are known and the catalog gets its lineage instead of a gap.
    assert "OOS Results PM" not in names
    for _, reason in emitted.omitted:
        assert reason, "a measure was omitted with no reason given"

    emitted_fields = {
        output
        for event in emitted.events
        for output in event["dataset"]["facets"]
        .get("columnLineage", {})
        .get("fields", {})
    }
    assert not (names & emitted_fields), "a measure was both omitted and emitted"


def test_the_output_is_json_a_consumer_can_load(emitted) -> None:
    import json

    parsed = json.loads(emitted.to_json())
    assert isinstance(parsed, list) and len(parsed) == len(emitted.events)


def test_the_namespace_can_be_set_to_whatever_a_catalog_uses(graph) -> None:
    """OpenLineage registers no scheme for a Power BI semantic model, so ours
    is a choice. A choice nobody can change is a constraint."""
    out = ol.emit(graph, namespace="acme-powerbi")
    assert all(e["dataset"]["namespace"] == "acme-powerbi" for e in out.events)
    inputs = [
        source
        for event in out.events
        for detail in event["dataset"]["facets"].get("columnLineage", {}).get("fields", {}).values()
        for source in detail["inputFields"]
    ]
    assert inputs and all(s["namespace"] == "acme-powerbi" for s in inputs)

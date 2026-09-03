"""Reading both of Power BI's report formats, and both spellings of a field.

Two findings, both from running the tool over Microsoft's published sample
library rather than over the files this project chose:

**Power BI changed its report format and we only read the old one.** Older
`.pbix` files carry the whole report in a single `Report/Layout` blob. Newer
ones (PBIR) carry a file per visual under
`Report/definition/pages/<page>/visuals/<id>/visual.json`. Reading only the
first reported "this file contains no report" about files that plainly have
one -- a confident wrong answer about somebody else's work.

**A bare `[Name]` is not always a measure.** DAX allows an unqualified column
reference, and real models use it: Microsoft's Store Sales sample writes
`AVERAGE([SellingAreaSize])`, where `SellingAreaSize` is a column on `Store`.
Treating every bare bracket as a measure refused 28 of that file's 32 measures
with "is not a measure in this model" -- about a model that has the thing.

The PBIR fixtures here are built rather than committed. A real sample in this
format is 9 MB, and what needs asserting is the shape of the reader, not the
contents of one file.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from concordance.normalize.layout import read_report

PBIR = "Report/definition/pages"


def _visual(entity: str, prop: str, kind: str = "Measure", role: str = "Values",
            aggregation: int | None = None) -> dict:
    field: dict = {
        kind: {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}
    }
    if aggregation is not None:
        field = {"Aggregation": {"Expression": field, "Function": aggregation}}
    return {
        "visual": {
            "visualType": "card",
            "visualContainerObjects": {
                "title": [{"properties": {"text": {"expr": {"Literal": {"Value": "'Total Sales'"}}}}}]
            },
            "query": {"queryState": {role: {"projections": [{"field": field}]}}},
        }
    }


def _write(path: Path, entries: dict[str, object]) -> zipfile.ZipFile:
    """A .pbix-shaped zip. `bytes` entries are written as-is, so the legacy
    blob can be UTF-16 exactly as Power BI writes it."""
    with zipfile.ZipFile(path, "w") as out:
        for name, body in entries.items():
            if isinstance(body, bytes):
                out.writestr(name, body)
            else:
                out.writestr(name, body if isinstance(body, str) else json.dumps(body))
    return zipfile.ZipFile(path)


# -- the newer per-file format -------------------------------------------------


def test_a_pbir_report_is_read(tmp_path) -> None:
    archive = _write(tmp_path / "m.pbix", {
        f"{PBIR}/pages.json": {"pageOrder": ["p1"]},
        f"{PBIR}/p1/page.json": {"name": "p1", "displayName": "Sales Overview"},
        f"{PBIR}/p1/visuals/v1/visual.json": _visual("Sales", "Total Sales"),
    })
    pages = read_report(archive)

    assert [p.name for p in pages] == ["Sales Overview"]
    tile = pages[0].visuals[0]
    assert tile.title == "Total Sales"
    assert tile.visual_type == "card"
    assert tile.fields[0].qualified_name == "Sales[Total Sales]"
    assert tile.fields[0].role == "Values"


def test_pages_keep_the_order_their_author_gave_them(tmp_path) -> None:
    """Not the order the zip happens to be sorted in.

    A report's pages are ordered by whoever built it, and that order is the only
    landmark a reader has for finding a tile again. `pages.json` declares it;
    sorting by folder name would put "ReportSection5..." before
    "ReportSectiona..." regardless of what the author intended.
    """
    archive = _write(tmp_path / "m.pbix", {
        f"{PBIR}/pages.json": {"pageOrder": ["zeta", "alpha"]},
        f"{PBIR}/zeta/page.json": {"displayName": "First"},
        f"{PBIR}/zeta/visuals/v/visual.json": _visual("Sales", "A"),
        f"{PBIR}/alpha/page.json": {"displayName": "Second"},
        f"{PBIR}/alpha/visuals/v/visual.json": _visual("Sales", "B"),
    })
    assert [p.name for p in read_report(archive)] == ["First", "Second"]


def test_a_page_the_order_forgot_still_appears(tmp_path) -> None:
    """Appended rather than dropped: a page nobody listed is still a page."""
    archive = _write(tmp_path / "m.pbix", {
        f"{PBIR}/pages.json": {"pageOrder": ["known"]},
        f"{PBIR}/known/page.json": {"displayName": "Known"},
        f"{PBIR}/known/visuals/v/visual.json": _visual("Sales", "A"),
        f"{PBIR}/orphan/page.json": {"displayName": "Orphan"},
        f"{PBIR}/orphan/visuals/v/visual.json": _visual("Sales", "B"),
    })
    assert [p.name for p in read_report(archive)] == ["Known", "Orphan"]


def test_an_aggregate_keeps_the_function_it_wraps(tmp_path) -> None:
    archive = _write(tmp_path / "m.pbix", {
        f"{PBIR}/pages.json": {"pageOrder": ["p"]},
        f"{PBIR}/p/page.json": {"displayName": "P"},
        f"{PBIR}/p/visuals/v/visual.json": _visual(
            "Sales", "Amount", kind="Column", aggregation=0
        ),
    })
    field = read_report(archive)[0].visuals[0].fields[0]
    assert field.aggregation == "Sum"
    assert field.qualified_name == "Sales[Amount]"


def test_furniture_is_still_not_a_tile(tmp_path) -> None:
    """A visual projecting nothing shows no number and cannot correlate."""
    archive = _write(tmp_path / "m.pbix", {
        f"{PBIR}/pages.json": {"pageOrder": ["p"]},
        f"{PBIR}/p/page.json": {"displayName": "P"},
        f"{PBIR}/p/visuals/button/visual.json": {"visual": {"visualType": "actionButton"}},
        f"{PBIR}/p/visuals/card/visual.json": _visual("Sales", "Total"),
    })
    tiles = read_report(archive)[0].visuals
    assert [t.visual_type for t in tiles] == ["card"]


def test_a_report_that_will_not_parse_costs_only_the_report(tmp_path) -> None:
    archive = _write(tmp_path / "m.pbix", {
        f"{PBIR}/pages.json": "{ not json at all",
        f"{PBIR}/p/page.json": "also not json",
    })
    assert read_report(archive) == []


def test_a_pbix_with_no_report_at_all_reads_as_none(tmp_path) -> None:
    archive = _write(tmp_path / "m.pbix", {"DataModel": "x"})
    assert read_report(archive) == []


# -- the older blob format still works ----------------------------------------


def test_the_legacy_format_is_still_read() -> None:
    """Both formats, not one replacing the other."""
    sample = Path("data/models/Sales_Returns_Sample.pbix")
    if not sample.exists():
        pytest.skip("sample not present")
    with zipfile.ZipFile(sample) as archive:
        assert "Report/Layout" in archive.namelist(), "this sample is the old format"
        pages = read_report(archive)
    assert len(pages) == 18
    assert sum(len(p.visuals) for p in pages) == 71


def test_the_legacy_blob_wins_when_both_could_be_present(tmp_path) -> None:
    """One file has one format. If both appear, the explicit blob is the report.

    Asserted so that adding the newer reader cannot silently change what an
    existing file resolves to.
    """
    layout = {
        "sections": [
            {
                "displayName": "Legacy",
                "visualContainers": [
                    {"config": json.dumps({
                        "singleVisual": {
                            "visualType": "card",
                            "projections": {"Values": [{"queryRef": "Sales.X"}]},
                            "prototypeQuery": {
                                "From": [{"Name": "s", "Entity": "Sales"}],
                                "Select": [{
                                    "Measure": {
                                        "Expression": {"SourceRef": {"Source": "s"}},
                                        "Property": "X",
                                    },
                                    "Name": "Sales.X",
                                }],
                            },
                        }
                    })}
                ],
            }
        ]
    }
    archive = _write(tmp_path / "m.pbix", {
        "Report/Layout": json.dumps(layout).encode("utf-16"),
        f"{PBIR}/pages.json": {"pageOrder": ["p"]},
        f"{PBIR}/p/page.json": {"displayName": "Newer"},
        f"{PBIR}/p/visuals/v/visual.json": _visual("Sales", "Y"),
    })
    assert [p.name for p in read_report(archive)] == ["Legacy"]


# -- a bare `[Name]` that is a column -----------------------------------------


def _tmdl(tmp_path: Path, measure: str, *, second_table: bool = False) -> str:
    """The smallest model that can hold the reference under test."""
    root = tmp_path / "M.SemanticModel" / "definition"
    (root / "tables").mkdir(parents=True)
    (root.parent / "definition" / "database.tmdl").write_text(
        "database M\n\tcompatibilityLevel: 1567\n", encoding="utf-8"
    )
    tables = ["ref table Sales", "ref table Metrics"]
    (root / "tables" / "Sales.tmdl").write_text(
        "table Sales\n\n\tcolumn SellingAreaSize\n\t\tdataType: int64\n"
        "\t\tsummarizeBy: sum\n\t\tsourceColumn: SellingAreaSize\n",
        encoding="utf-8",
    )
    if second_table:
        tables.append("ref table Store")
        (root / "tables" / "Store.tmdl").write_text(
            "table Store\n\n\tcolumn SellingAreaSize\n\t\tdataType: int64\n"
            "\t\tsummarizeBy: sum\n\t\tsourceColumn: SellingAreaSize\n",
            encoding="utf-8",
        )
    (root / "tables" / "Metrics.tmdl").write_text(
        "table Metrics\n\n\tcolumn Placeholder\n\t\tdataType: string\n"
        "\t\tisHidden\n\t\tsummarizeBy: none\n\t\tsourceColumn: Placeholder\n\n"
        f"\tmeasure Answer = {measure}\n",
        encoding="utf-8",
    )
    (root / "model.tmdl").write_text(
        "model Model\n\tculture: en-GB\n\n" + "\n".join(tables) + "\n",
        encoding="utf-8",
    )
    return str(tmp_path / "M.SemanticModel")


def test_a_bare_bracket_resolves_to_a_column_when_it_is_one(tmp_path) -> None:
    """`AVERAGE([SellingAreaSize])` is Microsoft's, not a contrivance.

    A bare `[Name]` usually means a measure, and sometimes means a column --
    Power BI accepts the unqualified form and real models use it. Assuming
    "measure" refused 28 of Store Sales' 32 measures with "is not a measure in
    this model", about a model that has the thing under a different kind.
    """
    from concordance.adapters.tmdl import TmdlAdapter
    from concordance.generate.sql import Status, translate

    model = TmdlAdapter().extract(_tmdl(tmp_path, "AVERAGE([SellingAreaSize])"))
    result = translate(model, model.measures[0])

    assert result.status is Status.EXACT, result.reason
    assert 'AVG("Sales"."SellingAreaSize")' in result.sql


def test_an_ambiguous_bare_bracket_is_refused_rather_than_guessed(tmp_path) -> None:
    """Two tables with the same column name is a genuine ambiguity.

    DAX resolves it from the row context the expression is evaluated in, which a
    query at a fixed grain does not have. Picking one by sort order would return
    a number from the wrong table and look completely normal doing it, so it is
    refused with both candidates named.
    """
    from concordance.adapters.tmdl import TmdlAdapter
    from concordance.generate.sql import Status, translate

    model = TmdlAdapter().extract(
        _tmdl(tmp_path, "AVERAGE([SellingAreaSize])", second_table=True)
    )
    result = translate(model, model.measures[0])

    assert result.status is not Status.EXACT
    assert result.sql == ""
    assert "Sales" in result.reason and "Store" in result.reason


def test_a_bare_bracket_naming_nothing_still_says_so(tmp_path) -> None:
    from concordance.adapters.tmdl import TmdlAdapter
    from concordance.generate.sql import Status, translate

    model = TmdlAdapter().extract(_tmdl(tmp_path, "AVERAGE([NoSuchThing])"))
    result = translate(model, model.measures[0])

    assert result.status is not Status.EXACT
    assert "not a measure or a column" in result.reason



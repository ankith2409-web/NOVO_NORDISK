"""Reading the filters a report applies before any of its numbers exist.

This module exists because of a question that looked like a bug and was not
one: Power BI showed `Net Sales` as 387.1K where this tool showed 1.2M, on the
same file, for the same measure. Both figures were right. Microsoft's report
pins every page to June; the tool was computing the same measure over every row
in the file. Nothing was wrong except that only one of the two questions was
visible, which on a tool whose whole claim is that a number can be checked is
the worst kind of silence.

So these tests are mostly about the *June* filter and the shapes around it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.normalize.filters import ReportFilter, read_filters

SALES_RETURNS = Path("data/models/Sales_Returns_Sample.pbix")


@pytest.fixture(scope="module")
def model():
    if not SALES_RETURNS.exists():
        pytest.skip(f"model not present: {SALES_RETURNS}")
    return PbixAdapter().extract(str(SALES_RETURNS))


def _texts(filters: list[ReportFilter]) -> list[str]:
    return [f.text for f in filters]


# -- the filter that explains the discrepancy ---------------------------------


def test_the_report_wide_june_filter_is_read(model) -> None:
    """The whole finding, in one assertion.

    Without this the tool reports 1.2M beside a card reading 387.1K and offers
    the reader nothing to reconcile them with.
    """
    report_wide = [f for f in model.report_filters if f.reaches_everything]
    assert _texts(report_wide) == ["Calendar[Date.Month] is June"]


def test_a_report_filter_is_marked_as_reaching_every_page(model) -> None:
    for found in model.report_filters:
        assert found.reaches_everything == (found.scope == "report")
        # A report-level filter belongs to no single page, and saying it did
        # would send a reader looking in the wrong place.
        if found.reaches_everything:
            assert found.page == ""


def test_each_page_filter_names_its_page(model) -> None:
    by_page = {f.page: f.text for f in model.report_filters if f.scope == "page"}
    assert by_page["Net Sales"] == "Sales[Status] is Sold"
    assert by_page["Returns"] == "Sales[Status] is Returned"
    assert by_page["Return Rate"] == "Product[Product] is OneNote"


def test_every_filter_below_report_level_says_where_it_lives(model) -> None:
    """A filter a reader cannot locate is a filter they cannot check.

    Page and visual scope answer different questions -- one narrows a whole
    page, the other narrows a single card while everything beside it stays
    wide -- so the scope has to be as visible as the sentence.
    """
    for found in model.report_filters:
        if found.reaches_everything:
            continue
        assert found.scope in {"page", "visual"}
        assert found.page, found.text
        # Only a visual filter belongs to a tile, and it always names one.
        assert bool(found.visual) == (found.scope == "visual"), found.text


def test_the_tile_filters_on_this_report_are_read(model) -> None:
    """Ten filters that sat on single tiles and were reported by nobody.

    A page filter is at least visible in the filter pane. One of these narrows
    a single card to the top 8 of a ranking while the card beside it stays
    wide, and until the visual scope existed the tool described both as
    unconditional.
    """
    on_tiles = [f for f in model.report_filters if f.scope == "visual"]
    assert len(on_tiles) == 10
    assert (
        "Association[RightItemSetId] is in the top 8 by Sum of Association[Importance]"
        in _texts(on_tiles)
    )


def test_every_filter_in_this_report_was_understood(model) -> None:
    assert model.report_filters
    unread = [f for f in model.report_filters if not f.readable]
    assert unread == [], _texts(unread)


# -- reading the shapes -------------------------------------------------------


def _document(expression: dict, where: list[dict]) -> dict:
    return {
        "filters": json.dumps(
            [{"name": "Filter", "expression": expression, "filter": {"Where": where}}]
        ),
        "sections": [],
    }


def _column(entity: str, prop: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def _in(*values: str) -> list[dict]:
    return [
        {
            "Condition": {
                "In": {
                    "Values": [[{"Literal": {"Value": f"'{value}'"}}] for value in values]
                }
            }
        }
    ]


def test_a_single_value_reads_as_a_sentence() -> None:
    found = read_filters(_document(_column("Sales", "Status"), _in("Sold")))
    assert _texts(found) == ["Sales[Status] is Sold"]


def test_several_values_read_as_a_list() -> None:
    found = read_filters(_document(_column("Store", "Chain"), _in("Lindseys", "Fama")))
    assert _texts(found) == ["Store[Chain] is one of Lindseys, Fama"]


def test_a_date_hierarchy_names_the_column_a_person_knows() -> None:
    """Power BI files a date filter under a generated table.

    `LocalDateTable_d9fbe243-...` is what the layout actually names, and
    printing that would be true and useless. The variation's own property is
    the column the author sees, so the filter reads `Calendar[Date.Month]`.
    """
    hierarchy = {
        "HierarchyLevel": {
            "Expression": {
                "Hierarchy": {
                    "Expression": {
                        "PropertyVariationSource": {
                            "Expression": {"SourceRef": {"Entity": "Calendar"}},
                            "Name": "Variation",
                            "Property": "Date",
                        }
                    },
                    "Hierarchy": "Date Hierarchy",
                }
            },
            "Level": "Month",
        }
    }
    found = read_filters(_document(hierarchy, _in("June")))
    assert _texts(found) == ["Calendar[Date.Month] is June"]
    assert "LocalDateTable" not in found[0].text


def test_a_negated_filter_says_so() -> None:
    where = [{"Condition": {"Not": {"Expression": {"In": {"Values": [[{"Literal": {"Value": "'Sold'"}}]]}}}}}]
    found = read_filters(_document(_column("Sales", "Status"), where))
    assert _texts(found) == ["Sales[Status] is not Sold"]


def test_a_comparison_reads_as_one() -> None:
    where = [
        {
            "Condition": {
                "Comparison": {
                    "ComparisonKind": 1,
                    "Right": {"Literal": {"Value": "1000L"}},
                }
            }
        }
    ]
    found = read_filters(_document(_column("Sales", "Amount"), where))
    assert _texts(found) == ["Sales[Amount] is more than 1000"]


def test_a_top_n_filter_reads_as_a_ranking() -> None:
    """Power BI writes "top 8" as membership of a subquery, not as an operator.

    Read only as far as the `In`, this looks like a list of values holding no
    values, which is how five real filters in Microsoft's own sample came to be
    reported as shapes this tool could not read.
    """
    where = [
        {
            "Condition": {
                "In": {
                    "Expressions": [_column("Association", "RightItemSetId")],
                    "Table": {"SourceRef": {"Source": "subquery"}},
                }
            }
        }
    ]
    document = {
        "filters": json.dumps(
            [
                {
                    "name": "Filter",
                    "expression": _column("Association", "RightItemSetId"),
                    "filter": {
                        "From": [
                            {
                                "Name": "subquery",
                                "Type": 2,
                                "Expression": {
                                    "Subquery": {
                                        "Query": {
                                            "From": [
                                                {"Name": "a", "Entity": "Association", "Type": 0}
                                            ],
                                            "OrderBy": [
                                                {
                                                    "Direction": 2,
                                                    "Expression": {
                                                        "Aggregation": {
                                                            "Function": 0,
                                                            "Expression": {
                                                                "Column": {
                                                                    "Expression": {
                                                                        "SourceRef": {"Source": "a"}
                                                                    },
                                                                    "Property": "Importance",
                                                                }
                                                            },
                                                        }
                                                    },
                                                }
                                            ],
                                            "Top": 8,
                                        }
                                    }
                                },
                            }
                        ],
                        "Where": where,
                    },
                }
            ]
        ),
        "sections": [],
    }
    assert _texts(read_filters(document)) == [
        "Association[RightItemSetId] is in the top 8 by Sum of Association[Importance]"
    ]


def test_a_bottom_n_filter_says_bottom() -> None:
    """Ascending order keeps the other end, and saying "top" would invert it."""
    document = {
        "filters": json.dumps(
            [
                {
                    "name": "Filter",
                    "expression": _column("Store", "Chain"),
                    "filter": {
                        "From": [
                            {
                                "Name": "q",
                                "Type": 2,
                                "Expression": {
                                    "Subquery": {
                                        "Query": {
                                            "From": [
                                                {"Name": "s", "Entity": "Store", "Type": 0}
                                            ],
                                            "OrderBy": [
                                                {
                                                    "Direction": 1,
                                                    "Expression": {
                                                        "Measure": {
                                                            "Expression": {
                                                                "SourceRef": {"Source": "s"}
                                                            },
                                                            "Property": "Net Sales",
                                                        }
                                                    },
                                                }
                                            ],
                                            "Top": 3,
                                        }
                                    }
                                },
                            }
                        ],
                        "Where": [
                            {
                                "Condition": {
                                    "In": {
                                        "Expressions": [_column("Store", "Chain")],
                                        "Table": {"SourceRef": {"Source": "q"}},
                                    }
                                }
                            }
                        ],
                    },
                }
            ]
        ),
        "sections": [],
    }
    assert _texts(read_filters(document)) == [
        "Store[Chain] is in the bottom 3 by Store[Net Sales]"
    ]


def test_a_source_alias_resolves_to_the_table_it_stands_for() -> None:
    """`"Source": "a"` is `FROM Association AS a`, not a table called "a"."""
    from concordance.normalize.filters import _entity_and_property

    aliased = {"Column": {"Expression": {"SourceRef": {"Source": "a"}}, "Property": "Importance"}}
    assert _entity_and_property(aliased, {"a": "Association"}) == ("Association", "Importance")
    # With nothing to resolve it against, it stays honestly anonymous rather
    # than reporting a table named "a".
    assert _entity_and_property(aliased) == ("", "Importance")


def test_an_untitled_custom_visual_is_described_not_identified() -> None:
    """A GUID and a build stamp are true and useless as a place to point at."""
    from concordance.normalize.filters import _kind

    assert _kind("PBI_CV_885EF3C3_31C1_4745_B2B9_20771D5AD196") == "a custom visual"
    assert _kind("ClusterMap1652434605854") == "ClusterMap"
    assert _kind("barChart") == "barChart"


# -- what it refuses to invent ------------------------------------------------


def test_an_empty_filter_card_is_not_reported_as_a_filter() -> None:
    """A filter sitting on the page with nothing selected narrows nothing.

    Reporting it would be a false alarm on a banner whose whole job is to
    explain a discrepancy, and false alarms are how a warning stops being read.
    """
    document = {
        "filters": json.dumps(
            [{"name": "Filter", "expression": _column("Sales", "Status"), "filter": {}}]
        ),
        "sections": [],
    }
    assert read_filters(document) == []


def test_an_unfamiliar_shape_is_reported_rather_than_dropped() -> None:
    """A filter nobody mentions is a filter nobody checks."""
    where = [{"Condition": {"SomeFutureOperator": {"whatever": True}}}]
    found = read_filters(_document(_column("Sales", "Amount"), where))
    assert len(found) == 1
    assert not found[0].readable
    assert "does not read" in found[0].text


def test_a_model_with_no_report_reports_no_filters() -> None:
    assert read_filters({}) == []
    assert read_filters({"filters": "", "sections": []}) == []
    assert read_filters({"filters": "not json", "sections": []}) == []
    assert read_filters(None) == []

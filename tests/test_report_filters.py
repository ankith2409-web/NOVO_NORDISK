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
    by_page = {f.page: f.text for f in model.report_filters if not f.reaches_everything}
    assert by_page["Net Sales"] == "Sales[Status] is Sold"
    assert by_page["Returns"] == "Sales[Status] is Returned"
    assert by_page["Return Rate"] == "Product[Product] is OneNote"


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

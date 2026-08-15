"""RLS and calculation groups out of a .pbix.

These were read for TMDL models and not for .pbix ones, which made the
guarantee depend on which format a model happened to be saved in. The original
reason was sound: every sample .pbix reports these tables with zero rows *and
no columns*, so their layout could not be learned by inspecting the data on
hand, and writing extraction against a guessed column list is exactly the
confidently-wrong work this project exists to catch.

What changed is not the samples but where the schema comes from. PBIXRay
builds these frames from SQL written out in its own source
(``meta/sqlite_source.py``), so the column names are a published contract:

    rls                          -> RoleName, RoleDescription, TableName,
                                    FilterExpression, State, MetadataPermission
    tmschema_calculation_groups  -> ID, TableName, TableID, Description,
                                    Precedence, ModifiedTime
    tmschema_calculation_items   -> ID, TableName, CalculationGroupID, Name,
                                    Description, Expression, Ordinal, ...

The frames below are shaped by that contract. They stand in for a .pbix this
repository does not have, the same way ``FakeProvider`` stands in for a
language model -- a test double for an interface, not invented findings.
"""

from __future__ import annotations

import pandas as pd

from concordance.adapters.pbix import build_calculation_groups, build_roles
from concordance.fingerprint import fingerprint_dax


def rls_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=[
            "TableName", "RoleName", "RoleDescription",
            "FilterExpression", "State", "MetadataPermission",
        ],
    )


# -- roles --------------------------------------------------------------------

def test_a_role_filtering_two_tables_becomes_one_role() -> None:
    """The frame is one row per (role, table) permission, so rows have to be
    grouped back -- two rows for one role is one role with two filters, not
    two roles."""
    roles = build_roles(
        rls_frame(
            [
                {
                    "TableName": "Sales", "RoleName": "EMEA",
                    "RoleDescription": "Europe only",
                    "FilterExpression": '[Region] = "EMEA"',
                    "State": "Ready", "MetadataPermission": None,
                },
                {
                    "TableName": "Customer", "RoleName": "EMEA",
                    "RoleDescription": "Europe only",
                    "FilterExpression": '[Region] = "EMEA"',
                    "State": "Ready", "MetadataPermission": None,
                },
            ]
        )
    )

    assert len(roles) == 1
    assert roles[0].name == "EMEA"
    assert roles[0].description == "Europe only"
    assert roles[0].restricted_tables == ("Sales", "Customer")


def test_each_filter_is_fingerprinted_as_the_dax_it_is() -> None:
    roles = build_roles(
        rls_frame(
            [
                {
                    "TableName": "Sales", "RoleName": "EMEA",
                    "RoleDescription": None,
                    "FilterExpression": '[Region] = "EMEA"',
                    "State": "Ready", "MetadataPermission": None,
                }
            ]
        )
    )
    assert roles[0].permissions[0].fingerprint == fingerprint_dax('[Region] = "EMEA"')


def test_a_reformatted_filter_is_not_a_change() -> None:
    def once(expression: str) -> str:
        return build_roles(
            rls_frame(
                [
                    {
                        "TableName": "Sales", "RoleName": "R",
                        "RoleDescription": None, "FilterExpression": expression,
                        "State": "Ready", "MetadataPermission": None,
                    }
                ]
            )
        )[0].permissions[0].fingerprint

    assert once('[Region]="EMEA"') == once('[Region]   =  "EMEA"')
    assert once('[Region]="EMEA"') != once('[Region]="APAC"')


def test_model_permission_is_left_empty_rather_than_defaulted() -> None:
    """PBIXRay's query never selects Role.ModelPermission. Filling in "read"
    because it is the common case would put a value in the document that was
    never read from the file -- the coverage gap says so instead."""
    roles = build_roles(
        rls_frame(
            [
                {
                    "TableName": "Sales", "RoleName": "R", "RoleDescription": None,
                    "FilterExpression": "[X] = 1", "State": "Ready",
                    "MetadataPermission": None,
                }
            ]
        )
    )
    assert roles[0].model_permission == ""


def test_an_absent_or_empty_frame_yields_no_roles() -> None:
    assert build_roles(None) == []
    assert build_roles(pd.DataFrame([])) == []


def test_rows_missing_a_role_or_table_are_skipped_not_guessed() -> None:
    roles = build_roles(
        rls_frame(
            [
                {
                    "TableName": "", "RoleName": "R", "RoleDescription": None,
                    "FilterExpression": "[X] = 1", "State": "Ready",
                    "MetadataPermission": None,
                },
                {
                    "TableName": "Sales", "RoleName": "", "RoleDescription": None,
                    "FilterExpression": "[X] = 1", "State": "Ready",
                    "MetadataPermission": None,
                },
            ]
        )
    )
    assert roles == []


# -- calculation groups -------------------------------------------------------

def groups_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["ID", "TableName", "TableID", "Description", "Precedence", "ModifiedTime"],
    )


def items_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=[
            "ID", "TableName", "CalculationGroupID", "Name",
            "Description", "Expression", "Ordinal",
        ],
    )


def test_items_are_joined_to_their_group_and_ordered() -> None:
    groups = build_calculation_groups(
        groups_frame(
            [{"ID": 1, "TableName": "Time", "TableID": 9,
              "Description": "Period lenses", "Precedence": 10, "ModifiedTime": ""}]
        ),
        items_frame(
            [
                {"ID": 2, "TableName": "Time", "CalculationGroupID": 1, "Name": "PY",
                 "Description": None,
                 "Expression": "CALCULATE(SELECTEDMEASURE(), SAMEPERIODLASTYEAR('D'[Date]))",
                 "Ordinal": 1},
                {"ID": 1, "TableName": "Time", "CalculationGroupID": 1, "Name": "YTD",
                 "Description": None,
                 "Expression": "TOTALYTD(SELECTEDMEASURE(), 'D'[Date])", "Ordinal": 0},
            ]
        ),
    )

    assert len(groups) == 1
    assert groups[0].table == "Time"
    assert groups[0].precedence == 10
    assert groups[0].description == "Period lenses"
    # Ordinal decides order, not the order rows happened to arrive in.
    assert [item.name for item in groups[0].items] == ["YTD", "PY"]
    assert groups[0].items[0].fingerprint == fingerprint_dax(
        "TOTALYTD(SELECTEDMEASURE(), 'D'[Date])"
    )


def test_a_group_with_no_items_is_still_reported() -> None:
    groups = build_calculation_groups(
        groups_frame([{"ID": 1, "TableName": "Empty", "TableID": 9,
                       "Description": None, "Precedence": None, "ModifiedTime": ""}]),
        items_frame([]),
    )
    assert len(groups) == 1
    assert groups[0].items == ()
    assert groups[0].precedence is None


def test_pandas_missing_values_do_not_leak_into_the_document() -> None:
    """A frame's absent cell arrives as NaN, and `str(nan)` is the string
    "nan" -- which would read as a real description in a generated document."""
    groups = build_calculation_groups(
        groups_frame([{"ID": 1, "TableName": "T", "TableID": 9,
                       "Description": float("nan"), "Precedence": float("nan"),
                       "ModifiedTime": ""}]),
        items_frame([]),
    )
    assert groups[0].description is None
    assert groups[0].precedence is None


def test_absent_frames_yield_no_groups() -> None:
    assert build_calculation_groups(None, None) == []

"""Regression test for a pandas-NaN truthiness bug.

Found during a post-push audit, not by any of the three sample models -- none
of them happens to have a measure with a missing expression, so this needs no
.pbix file at all, just a row shaped the way PBIXRay would hand it back.

``row.get("Expression") or ""`` looks like a safe default, but a pandas/numpy
NaN is truthy in Python (``bool(float("nan"))`` is ``True``), so a genuinely
missing expression would silently become the three-character string ``"nan"``
and get fingerprinted as if it were real DAX -- exactly the kind of quiet
corruption a fingerprint-based drift system cannot tolerate.
"""

from __future__ import annotations

from janus.adapters.pbix import PbixAdapter


def test_nan_expression_becomes_empty_not_the_string_nan() -> None:
    row = {
        "TableName": "Broken",
        "Name": "Placeholder Measure",
        "Expression": float("nan"),
        "DisplayFolder": float("nan"),
        "Description": float("nan"),
    }
    measure = PbixAdapter()._build_measure(row, known_measures=set())

    assert measure.expression == ""
    assert measure.display_folder is None
    assert measure.description is None
    # An empty expression must not be quietly fingerprinted as if it meant
    # something -- it should still hash (nothing downstream may crash on it),
    # just not to whatever "nan" would have hashed to.
    from janus.fingerprint import fingerprint_dax
    assert measure.fingerprint == fingerprint_dax("")


def test_the_trap_this_guards_against_is_real() -> None:
    """Documents *why* `x or default` was wrong, independent of any JANUS code.

    `float("nan")` is truthy in Python, so `nan or ""` returns the NaN itself
    rather than falling through to the default -- the opposite of what the
    `or` idiom is normally used for.
    """
    assert bool(float("nan")) is True
    assert (float("nan") or "fallback") != "fallback"


def test_real_expression_is_unaffected_by_the_nan_guard() -> None:
    row = {
        "TableName": "Sales",
        "Name": "Net Sales",
        "Expression": 'SUM(Sales[Amount])',
        "DisplayFolder": None,
        "Description": None,
    }
    measure = PbixAdapter()._build_measure(row, known_measures=set())
    assert measure.expression == "SUM(Sales[Amount])"


def test_power_query_skips_a_table_with_a_nan_expression() -> None:
    import pandas as pd

    frame = pd.DataFrame(
        [{"TableName": "Broken", "Expression": float("nan")}]
    )
    result = PbixAdapter()._power_query_by_table(_FakeRaw(frame))
    assert "Broken" not in result


class _FakeRaw:
    """Minimal stand-in exposing only the attribute `_power_query_by_table` reads."""

    def __init__(self, power_query) -> None:
        self.power_query = power_query

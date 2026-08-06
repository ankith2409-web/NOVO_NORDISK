"""Tests for the DAX lexer and canonicaliser.

These are the tests that matter most in the whole system: if canonicalisation is
wrong, every fingerprint is wrong, and drift detection reports noise or silence
instead of findings.
"""

from __future__ import annotations

import pytest

from janus.normalize.dax import Kind, canonicalise, extract_references, tokenize


# -- things that must NOT change the canonical form -------------------------

@pytest.mark.parametrize(
    "a,b",
    [
        # whitespace and line breaks
        ("SUM(Sales[Amount])", "SUM (\n    Sales[Amount]\n)"),
        # casing of function names
        ("SUM(Sales[Amount])", "sum(Sales[Amount])"),
        # casing of object names -- case-insensitive in DAX
        ("SUM(Sales[Amount])", "SUM(sales[amount])"),
        # table qualifier quoting
        ("SUM(Sales[Amount])", "SUM('Sales'[Amount])"),
        # line comments, both syntaxes
        ("SUM(Sales[Amount])", "SUM(Sales[Amount]) // total\n"),
        ("SUM(Sales[Amount])", "SUM(Sales[Amount]) -- total\n"),
        # block comments, including mid-expression
        ("SUM(Sales[Amount])", "SUM(/* the money column */ Sales[Amount])"),
        # equivalent numeric spellings
        ("IF([X] > 1, 1, 0)", "IF([X] > 1.0, 1.00, 0)"),
        ("IF([X] > 1000, 1, 0)", "IF([X] > 1e3, 1, 0)"),
    ],
)
def test_equivalent_expressions_share_canonical_form(a: str, b: str) -> None:
    assert canonicalise(a) == canonicalise(b)


# -- things that MUST change it ---------------------------------------------

@pytest.mark.parametrize(
    "a,b",
    [
        # a different filter value is a different measure
        ('CALCULATE([S], T[Status]="Sold")', 'CALCULATE([S], T[Status]="Returned")'),
        # string literals are case-sensitive data, not object names
        ('CALCULATE([S], T[Status]="Sold")', 'CALCULATE([S], T[Status]="sold")'),
        # different column
        ("SUM(Sales[Amount])", "SUM(Sales[Cost])"),
        # different table
        ("SUM(Sales[Amount])", "SUM(Budget[Amount])"),
        # different aggregation
        ("SUM(Sales[Amount])", "AVERAGE(Sales[Amount])"),
        # a real numeric threshold change
        ("IF([X] > 1, 1, 0)", "IF([X] > 2, 1, 0)"),
        # added filter -- the case from the architecture diagram
        (
            "SUMX(Sales, Sales[Qty] * Sales[Price])",
            "SUMX(FILTER(Sales, Sales[Returned]=0), Sales[Qty] * Sales[Price])",
        ),
        # operator direction
        ("IF([X] > 1, 1, 0)", "IF([X] < 1, 1, 0)"),
        ("IF([X] >= 1, 1, 0)", "IF([X] > 1, 1, 0)"),
    ],
)
def test_different_meaning_changes_canonical_form(a: str, b: str) -> None:
    assert canonicalise(a) != canonicalise(b)


# -- lexer edge cases --------------------------------------------------------

def test_comment_markers_inside_a_string_are_not_comments() -> None:
    """The reason this is a lexer and not a regex."""
    expr = 'IF(T[Path]="//server--prod", 1, 0)'
    assert "//server--prod" in canonicalise(expr)


def test_doubled_quote_is_an_escape_not_a_terminator() -> None:
    tokens = [t for t in tokenize('T[C]="say ""hi"" now"') if t.kind is Kind.STRING]
    assert len(tokens) == 1
    assert tokens[0].value == 'say "hi" now'


def test_escaped_closing_bracket_in_column_name() -> None:
    tokens = [t for t in tokenize("T[odd]]name]") if t.kind is Kind.BRACKET_REF]
    assert tokens[0].value == "odd]name"


def test_table_name_containing_spaces_survives() -> None:
    canonical = canonicalise("SUM('Analysis DAX'[Net Sales])")
    assert "'analysis dax'" in canonical
    assert "[net sales]" in canonical


def test_double_minus_is_a_comment_but_subtraction_is_not() -> None:
    assert canonicalise("[A] -- comment\n") == canonicalise("[A]")
    assert canonicalise("[A] - [B]") != canonicalise("[A]")


def test_unterminated_string_does_not_hang_or_raise() -> None:
    assert canonicalise('T[C]="unterminated') != ""


def test_canonicalisation_is_idempotent() -> None:
    expr = "CALCULATE( SUM('Sales'[Amount]), 'Sales'[Status] = \"Sold\" )"
    once = canonicalise(expr)
    assert canonicalise(once) == once


# -- reference extraction ----------------------------------------------------

def test_extracts_qualified_columns_and_bare_measures() -> None:
    refs = extract_references("CALCULATE([Net Sales], 'Calendar'[Date] > 1)")
    assert ("Calendar", "Date") in refs.columns
    assert "Net Sales" in refs.unqualified


def test_a_string_that_looks_like_a_reference_is_not_one() -> None:
    refs = extract_references('IF(T[C] = "Sales[Amount]", 1, 0)')
    assert ("Sales", "Amount") not in refs.columns
    assert ("T", "C") in refs.columns

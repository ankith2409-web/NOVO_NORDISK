"""Rendering a figure the way the file says it should be rendered.

These tests exist because of a sentence this tool used to print under its own
KPI cards: that Power BI renders `0.42` as `42.29%` "using a format string this
file does not expose". The file exposes it. Three of the sample models declare
one on almost every measure, and the tool was showing raw ratios beside a
report showing percentages while asserting the information was not there.

So the tests come in two halves, and the second is the important one. The first
checks the formats it renders. The second checks the ones it *declines* --
because the failure mode this replaces is not "unformatted", it is
"confidently formatted wrong", and a number that has been silently mangled is
worse than one shown exactly as the query returned it.
"""

from __future__ import annotations

import pytest

from concordance.generate.formats import describe, parse, render

#: What Microsoft's own samples declare, verbatim from their `Measure` tables.
CURRENCY = "\\$#,0;-\\$#,0;\\$#,0"
BRACKETED = "\\$#,0;(\\$#,0);\\$#,0"
PERCENT = "0.0%;-0.0%;0.0%"
GENERAL = "\\$#,0.###############;(\\$#,0.###############);\\$#,0.###############"


# -- the finding itself --------------------------------------------------------


def test_a_ratio_renders_as_the_percentage_power_bi_shows() -> None:
    """The exact case the old disclaimer described and declined to handle."""
    assert render(0.4229, PERCENT) == "42.3%"


def test_the_june_figure_renders_as_currency() -> None:
    """387144 is what Power BI's card reads once the report's June filter is
    applied; `$387,144` is what it reads on screen."""
    assert render(387144.0, CURRENCY) == "$387,144"


# -- the shapes ----------------------------------------------------------------


def test_a_negative_uses_its_own_section() -> None:
    """An author who writes brackets for negatives has said how to show one.

    The sign is not printed as well -- the section already carries it, and
    `-($1,248,013)` states it twice.
    """
    assert render(-1248013.0, BRACKETED) == "($1,248,013)"
    assert render(-1248013.0, CURRENCY) == "-$1,248,013"


def test_zero_uses_its_own_section() -> None:
    assert render(0.0, BRACKETED) == "$0"


def test_a_hash_is_an_optional_digit_and_a_zero_is_a_required_one() -> None:
    """The difference between the two placeholders, which is where the
    general-number format went wrong: fifteen `#` are up to fifteen decimal
    places, not fifteen forced ones."""
    assert render(1234.5678, GENERAL) == "$1,234.5678"
    assert render(1234.0, GENERAL) == "$1,234"
    # `0.00` forces both places even when the value has neither.
    assert render(1234.0, "\\$#,0.00;(\\$#,0.00);\\$#,0.00") == "$1,234.00"


def test_a_figure_is_not_given_the_residue_of_storing_it() -> None:
    """`0.4229 * 100` is `42.290000000000006` in binary floating point.

    Printed to fifteen places that residue looks like precision the file asked
    for, which is a number a reader cannot check against anything.
    """
    assert render(0.4229, "0.###############%") == "42.29%"
    assert "0000000" not in (render(1234.5678, GENERAL) or "")


def test_grouping_is_read_from_the_picture_not_assumed() -> None:
    assert render(1234567.0, "#,0") == "1,234,567"
    assert render(1234567.0, "0") == "1234567"


def test_a_quoted_literal_is_printed_as_written() -> None:
    assert render(1234.0, '"USD "#,0') == "USD 1,234"


def test_a_single_section_covers_every_sign() -> None:
    assert render(-5.0, "#,0") == "-5"
    assert render(0.0, "#,0") == "0"


# -- what it declines ----------------------------------------------------------


@pytest.mark.parametrize(
    "format_string",
    [
        "Long Date",  # Calendar[Date] in Sales & Returns
        "dd-mmm-yyyy",  # Store[Opening date] in Store Sales
        "mmm-yyyy",  # Fiscal calendar[Month] in Store Sales
    ],
)
def test_a_date_picture_is_declined_rather_than_read_as_a_number(
    format_string: str,
) -> None:
    """All three appear in the sample files, on real columns.

    A date format applied to a number is not a formatting difference, it is a
    different value, so there is no version of guessing here that is better
    than showing the figure as it stands.
    """
    assert render(1234.5, format_string) is None
    assert describe(format_string) == ""


def test_a_scaling_comma_is_declined_because_it_changes_the_figure() -> None:
    """A trailing comma is VBA's divide-by-a-thousand, not a separator.

    Reading it as a separator would report 1,234 where the file asked for 1 --
    the one class of error this whole module exists to avoid.
    """
    assert render(1234.0, "#,0,") is None


def test_a_format_with_no_digits_at_all_is_declined() -> None:
    """`"Sold"` is a legal format string and not a number picture. Rendering a
    figure as a fixed word replaces it rather than presenting it."""
    assert render(1234.0, '"Sold"') is None
    assert render(1234.0, "") is None
    assert render(1234.0, "   ") is None


def test_more_than_three_sections_is_declined() -> None:
    """Four sections means a conditional format, whose conditions this does not
    evaluate. Applying the first one regardless would be a guess."""
    assert render(1234.0, "#,0;#,0;#,0;#,0") is None


@pytest.mark.parametrize(
    "format_string",
    [
        "0.00E+00",  # scientific notation, which this does not produce
        "[Red]#,0",  # a colour section, whose name would print as a prefix
        "@",  # the text placeholder
        "0.00;;",  # VBA's "hide negatives", which is a rule not a picture
    ],
)
def test_a_picture_with_digits_in_an_unread_shape_is_still_declined(
    format_string: str,
) -> None:
    """The dangerous half of the declining.

    A date picture has no digit placeholders in it, so it would be turned away
    even with no check for unfamiliar shapes at all. These do have digits, and
    without that check `0.00E+00` renders 1234.5 as `1234.50E+00` and
    `[Red]#,0` renders it as `[Red]1,235` -- confidently, and wrongly.
    """
    assert render(1234.5, format_string) is None


def test_declining_is_a_return_value_and_never_an_exception() -> None:
    """A caller reads None as "show the figure as the query returned it". A
    caller that has to catch an exception instead tends not to."""
    for odd in ("\\", ";", ";;;", "#" * 400, "0.0" + "0" * 500):
        assert render(1234.5, odd) is None or isinstance(render(1234.5, odd), str)


# -- saying what a format means ------------------------------------------------


def test_a_format_string_is_described_in_words() -> None:
    """`\\$#,0;-\\$#,0;\\$#,0` in a BRD has told a business reader nothing."""
    assert describe(CURRENCY) == "a currency amount, no decimal places, grouped in thousands"
    assert describe(PERCENT) == "a percentage, 1 decimal place"
    assert describe("#,0.00") == "a number, 2 decimal places, grouped in thousands"
    assert describe(GENERAL).startswith("a currency amount, up to 15 decimal places")


def test_parse_reports_the_sections_a_format_declares() -> None:
    positive, negative, zero = parse(BRACKETED)
    assert positive.prefix == "$" and positive.suffix == ""
    assert negative is not None and negative.prefix == "($"
    assert zero is not None and zero.prefix == "$"
    assert parse(CURRENCY) is not None
    assert parse("Long Date") is None


# -- against the files themselves ----------------------------------------------


def test_every_numeric_format_in_the_sample_models_is_renderable() -> None:
    """The measure of whether this reads the files rather than a subset of them.

    Anything declined here has to be a date picture -- those are a different
    kind of format, not an unread one -- and if a numeric shape ever appears
    that this cannot render, this test is where it should be noticed.
    """
    from pathlib import Path

    from concordance.adapters.pbix import PbixAdapter

    found: dict[str, str] = {}
    for name in ("Sales_Returns_Sample", "StoreSales", "Supply_Chain_Sample"):
        path = Path(f"data/models/{name}.pbix")
        if not path.exists():
            pytest.skip(f"model not present: {path}")
        model = PbixAdapter().extract(str(path))
        for measure in model.measures:
            if measure.format_string:
                found.setdefault(measure.format_string, measure.qualified_name)
        for column in model.columns:
            if column.format_string:
                found.setdefault(column.format_string, column.qualified_name)

    assert found, "no format strings were read at all"
    declined = {f: who for f, who in found.items() if render(1234.5, f) is None}
    # The three date pictures, and nothing else.
    assert sorted(declined) == ["Long Date", "dd-mmm-yyyy", "mmm-yyyy"], declined


# -- the same figure at the size a card is --------------------------------------


def test_an_abbreviated_figure_keeps_what_the_format_said_it_is() -> None:
    """`1.25M` where the file said currency has become a count on the way.

    The prefix is the part that says what kind of number this is, so it is the
    part an abbreviation must not drop.
    """
    from concordance.generate.formats import compact

    assert compact(1248013.0, CURRENCY) == "$1.25M"
    assert compact(45_180_000_000.0, CURRENCY) == "$45.18B"


def test_a_small_figure_is_not_abbreviated() -> None:
    from concordance.generate.formats import compact

    assert compact(387144.0, CURRENCY) == "$387,144"
    assert compact(12.5, "#,0.00") == "12.50"


def test_a_percentage_is_never_abbreviated() -> None:
    """It is already small, and `42.3%` shortened is `42.3%`."""
    from concordance.generate.formats import compact

    assert compact(0.4229, PERCENT) == "42.3%"


def test_an_unreadable_format_abbreviates_to_nothing_rather_than_guessing() -> None:
    from concordance.generate.formats import compact

    assert compact(1248013.0, "dd-mmm-yyyy") is None

"""Rendering a figure the way the file says it should be rendered.

This module exists because of a sentence the tool used to print under its own
KPI cards:

    Power BI renders a ratio like 0.42 as 42.29% using a format string this
    file does not expose, so reinterpreting one here would be a guess.

Every clause of that is defensible except the factual one, and the factual one
was wrong. The file does expose it. `Net Sales` declares
`\\$#,0;-\\$#,0;\\$#,0`; `Net Sales Variance %` declares `0.0%;-0.0%;0.0%`;
`% on backorder` declares `0.00%;-0.00%;0.00%`. They sit in the model's own
`Measure` table, which the reader this project uses queries five columns of and
`FormatString` is not one of them. So the tool showed `0.4229` beside a report
showing `42.29%`, and explained the difference by asserting something about the
file that reading the file disproves.

That is worse than showing the raw number, because a reader who is told the
information is absent stops looking for it.

What this does *not* do is implement the whole of VBA's format syntax. Power BI
inherits a language with date pictures, scientific notation, colour names and
conditional sections, and a half-implementation that silently renders the
hard cases wrong would put this project back where it started: a figure that
cannot be checked. So the subset below is the one that covers real measures --
sign sections, digit placeholders, grouping, percent, currency and literal
text -- and anything outside it returns `None`, which every caller reads as
"show the number as the query returned it".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

#: Characters a format section may contain and still be one this can render.
#: Anything else -- `E+` exponents, `@` text placeholders, `[Red]` colours,
#: `yyyy` date pictures -- means the section is not a number picture of the
#: kind read here, so the whole format is declined rather than half-applied.
_UNDERSTOOD = re.compile(r'^[#0.,%$()\\\s\'"+£€¥-]*$')

#: A run of digit placeholders, which is the part that actually says how many
#: digits to print. Everything around it is literal text. It has to contain at
#: least one placeholder: a pattern that can match the empty string matches it
#: at position 0 of every format string, which made `\$#,0` look like a
#: picture with no digits in it and declined every currency measure in the
#: three sample files.
_DIGITS = re.compile(r"[#0,]*[#0][#0,]*(?:\.[#0]*)?")

#: An escaped character or a quoted run -- the two ways a format string says
#: "print this literally". They are removed before the section is checked for
#: shapes this cannot render, because a quoted literal is allowed to contain
#: anything at all and `"USD "#,0` is a number picture however it looks.
_LITERAL = re.compile(r"\\.|\"[^\"]*\"|'[^']*'")


@dataclass(frozen=True)
class Picture:
    """One section of a format string, taken apart."""

    prefix: str
    suffix: str
    #: How many decimal places must always be printed -- the `0` placeholders.
    least: int
    #: How many may be printed -- `0` and `#` together. `#` is an *optional*
    #: digit, and reading it as a required one is what made Power BI's own
    #: general-number format, `\$#,0.###############`, render $1,234.5678 as
    #: `$1,234.567800000000034`: fifteen forced decimal places of a float's
    #: binary residue, presented as if the file had asked for them.
    most: int
    grouped: bool
    percent: bool


def _unescape(text: str) -> str:
    """Literal text, with the format language's own escaping removed.

    `\\$` is a dollar sign the author escaped so it would not be read as a
    currency code; `"USD "` is a quoted literal. Both are meant to be printed.
    """
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            out.append(text[index + 1])
            index += 2
            continue
        if char in "\"'":
            close = text.find(char, index + 1)
            if close == -1:
                index += 1
                continue
            out.append(text[index + 1 : close])
            index = close + 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _picture(section: str) -> Picture | None:
    """One section as prefix, digits and suffix, or None when unfamiliar."""
    if not _UNDERSTOOD.match(_LITERAL.sub("", section)):
        return None

    match = _DIGITS.search(section)
    digits = match.group(0) if match else ""
    if not digits.strip(",."):
        # No digit placeholders at all. `"Sold"` is a legal format string and
        # a legal answer, but it is not a number picture, and rendering a
        # figure as a fixed word is a change of meaning rather than of style.
        return None

    prefix = _unescape(section[: match.start()])
    suffix = _unescape(section[match.end() :])

    whole, _, fraction = digits.partition(".")
    return Picture(
        prefix=prefix,
        suffix=suffix,
        least=fraction.count("0"),
        most=len(fraction),
        # A comma between placeholders is a thousands separator. A comma at the
        # end is VBA's "scale down by a thousand", which is a different feature
        # and not one this claims to implement -- `_UNDERSTOOD` lets it through
        # but `whole.endswith(",")` catches it here.
        grouped="," in whole.strip(","),
        percent="%" in prefix or "%" in suffix,
    )


def parse(format_string: str) -> tuple[Picture, Picture | None, Picture | None] | None:
    """A format string as its positive, negative and zero pictures.

    Power BI writes the three separated by semicolons, and an author who wants
    negatives in brackets says so there -- `\\$#,0;(\\$#,0);\\$#,0`. Only the
    first is required.
    """
    if not format_string or not format_string.strip():
        return None

    sections = format_string.split(";")
    if len(sections) > 3:
        return None
    if any(section.strip().endswith(",") for section in sections):
        return None
    if any(not section.strip() for section in sections[1:]):
        # `0.00;;` is VBA for "print nothing at all for a negative or a zero".
        # That is a rule about visibility rather than a picture of a number,
        # and the tempting reading -- fall back to the positive section --
        # prints -1234.5 as `1234.50`, losing the sign. Declining shows the
        # figure as the query returned it, which is at least the right number.
        return None

    positive = _picture(sections[0])
    if positive is None:
        return None

    def optional(index: int) -> Picture | None:
        return _picture(sections[index]) if len(sections) > index else None

    return positive, optional(1), optional(2)


def render(value: float, format_string: str) -> str | None:
    """`0.4229` and `0.0%;-0.0%;0.0%` -> `42.3%`. None when unsure.

    Returning None rather than a best effort is the whole discipline here: a
    caller that gets None prints the figure the query returned, which is always
    true even when it is not what Power BI would show. A caller that gets a
    string can say the file asked for it.
    """
    pictures = parse(format_string)
    if pictures is None:
        return None

    positive, negative, zero = pictures
    if value == 0 and zero is not None:
        picture = zero
    elif value < 0 and negative is not None:
        picture = negative
        # A negative section states its own sign -- as a literal minus, or by
        # wrapping in brackets. Handing it a negative number as well would
        # print it twice.
        value = -value
    else:
        picture = positive

    body = _digits(value, picture)
    if body is None:
        return None
    return f"{picture.prefix}{body}{picture.suffix}"


def _digits(value: float, picture: Picture) -> str | None:
    """The number itself, to as many places as the picture asks for.

    Done in `Decimal` rather than in float arithmetic, because both steps here
    introduce error a reader would see: `0.4229 * 100` is `42.290000000000006`
    in binary floating point, and printing a value to fifteen places shows the
    residue of storing it at all. `str()` of a float is its shortest
    round-tripping form, so starting from that keeps the figure the query
    returned and nothing else.
    """
    try:
        number = Decimal(str(value))
        if picture.percent:
            number *= 100
        number = number.quantize(
            Decimal(1).scaleb(-picture.most), rounding=ROUND_HALF_UP
        )
    except (ArithmeticError, InvalidOperation, ValueError):
        # A figure too large to place to that many decimals. Declining is the
        # same answer this gives to any format it cannot honour exactly.
        return None

    text = format(number, ",f" if picture.grouped else "f")
    if picture.most > picture.least and "." in text:
        whole, _, fraction = text.partition(".")
        fraction = fraction.rstrip("0").ljust(picture.least, "0")
        text = f"{whole}.{fraction}" if fraction else whole
    return text


def describe(format_string: str) -> str:
    """What a format string does, for a reader who does not read VBA.

    A BRD saying `\\$#,0;-\\$#,0;\\$#,0` has told a business reader nothing.
    Saying "a currency amount, no decimal places" has told them what the number
    on the page means.
    """
    pictures = parse(format_string)
    if pictures is None:
        return ""

    picture = pictures[0]
    literals = f"{picture.prefix}{picture.suffix}".replace("%", "").strip()
    if picture.percent:
        kind = "a percentage"
    elif any(symbol in literals for symbol in "$£€¥"):
        kind = "a currency amount"
    else:
        kind = "a number"

    def plural(count: int) -> str:
        return f"{count} decimal place" + ("" if count == 1 else "s")

    if picture.most == 0:
        places = "no decimal places"
    elif picture.least == picture.most:
        places = plural(picture.most)
    elif picture.least == 0:
        places = f"up to {plural(picture.most)}"
    else:
        places = f"{picture.least} to {plural(picture.most)}"
    grouped = ", grouped in thousands" if picture.grouped else ""
    return f"{kind}, {places}{grouped}"


#: Where a figure stops fitting on a card and starts wanting an abbreviation.
_UNITS = ((1_000_000_000, "B"), (1_000_000, "M"))


def compact(value: float, format_string: str) -> str | None:
    """The same figure at the size a KPI card is: `$1.25M`, not `$1,248,013`.

    A card abbreviates because it has three centimetres to work with, and the
    abbreviation has to keep whatever the format said the number *is* -- a
    currency amount shortened to `1.25M` has quietly become a count. So the
    picture's prefix and suffix are kept and only the digits are scaled.

    Percentages are never abbreviated: they are already small, and `42.3%`
    shortened is `42.3%`.
    """
    pictures = parse(format_string)
    if pictures is None:
        return None

    picture = pictures[0]
    if picture.percent or value < 0:
        # A negative may have a section of its own, with its own brackets, and
        # scaling the digits inside one is more care than a card is worth.
        return render(value, format_string)

    for size, unit in _UNITS:
        if value >= size:
            return f"{picture.prefix}{value / size:,.2f}{unit}{picture.suffix}"
    return render(value, format_string)

"""DAX lexer and canonicaliser.

The fingerprint scheme depends entirely on this module: two expressions that mean
the same thing must canonicalise to the same string, and two that mean different
things must not. That rules out regex-based normalisation, which cannot tell a
``//`` inside a string literal from a comment, so the input is properly lexed
first and re-serialised from the token stream.

Canonical form is the token stream with comments and whitespace dropped, object
names case-folded (DAX object names are case-insensitive), table qualifiers
always quoted, numeric literals in a single form, and string literals preserved
byte for byte -- ``"Sold"`` and ``"sold"`` are different filter values and must
never collapse onto the same fingerprint.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum


class Kind(Enum):
    STRING = "string"          # "..."  -- a literal value, case-sensitive
    QUOTED_IDENT = "qident"    # '...'  -- a table name
    BRACKET_REF = "bref"       # [...]  -- a column or measure reference
    IDENT = "ident"            # bare word: function name, keyword, or table name
    NUMBER = "number"
    OP = "op"
    COMMENT = "comment"
    WS = "ws"


@dataclass(frozen=True)
class Token:
    kind: Kind
    value: str   # inner text for delimited kinds, raw text otherwise
    raw: str     # exactly as it appeared in the source


_OP_CHARS = set("+-*/^=<>&,(){}:.")
_IDENT_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_IDENT_CONT = _IDENT_START | set("0123456789")


def tokenize(expr: str) -> list[Token]:
    """Lex a DAX expression into tokens, preserving everything needed to rebuild it."""
    tokens: list[Token] = []
    i, n = 0, len(expr)

    while i < n:
        ch = expr[i]

        # -- whitespace -------------------------------------------------
        if ch.isspace():
            j = i
            while j < n and expr[j].isspace():
                j += 1
            tokens.append(Token(Kind.WS, expr[i:j], expr[i:j]))
            i = j
            continue

        # -- comments ---------------------------------------------------
        # Both // and -- start a line comment in DAX. `--` is only a comment
        # when doubled; `a - -b` stays arithmetic.
        if expr.startswith("//", i) or expr.startswith("--", i):
            j = expr.find("\n", i)
            j = n if j == -1 else j
            tokens.append(Token(Kind.COMMENT, expr[i:j], expr[i:j]))
            i = j
            continue

        if expr.startswith("/*", i):
            j = expr.find("*/", i + 2)
            j = n if j == -1 else j + 2
            tokens.append(Token(Kind.COMMENT, expr[i:j], expr[i:j]))
            i = j
            continue

        # -- delimited forms --------------------------------------------
        # Scanned before anything else so that a // or -- inside them is
        # never mistaken for a comment.
        if ch == '"':
            value, j = _scan_delimited(expr, i, '"')
            tokens.append(Token(Kind.STRING, value, expr[i:j]))
            i = j
            continue

        if ch == "'":
            value, j = _scan_delimited(expr, i, "'")
            tokens.append(Token(Kind.QUOTED_IDENT, value, expr[i:j]))
            i = j
            continue

        if ch == "[":
            value, j = _scan_bracket(expr, i)
            tokens.append(Token(Kind.BRACKET_REF, value, expr[i:j]))
            i = j
            continue

        # -- numbers ----------------------------------------------------
        if ch.isdigit() or (ch == "." and i + 1 < n and expr[i + 1].isdigit()):
            j = _scan_number(expr, i)
            tokens.append(Token(Kind.NUMBER, expr[i:j], expr[i:j]))
            i = j
            continue

        # -- bare identifiers -------------------------------------------
        if ch in _IDENT_START:
            j = i
            while j < n and expr[j] in _IDENT_CONT:
                j += 1
            tokens.append(Token(Kind.IDENT, expr[i:j], expr[i:j]))
            i = j
            continue

        # -- operators and punctuation ----------------------------------
        if ch in _OP_CHARS:
            # Two-character comparison operators must stay together.
            if expr.startswith((">=", "<=", "<>"), i):
                tokens.append(Token(Kind.OP, expr[i:i + 2], expr[i:i + 2]))
                i += 2
                continue
            tokens.append(Token(Kind.OP, ch, ch))
            i += 1
            continue

        # Anything unrecognised is kept verbatim rather than dropped, so an
        # unusual expression degrades to a stable fingerprint instead of a
        # silently wrong one.
        tokens.append(Token(Kind.OP, ch, ch))
        i += 1

    return tokens


def _scan_delimited(expr: str, start: int, quote: str) -> tuple[str, int]:
    """Scan a quoted run starting at ``start``. A doubled quote is an escape."""
    i = start + 1
    n = len(expr)
    out: list[str] = []
    while i < n:
        if expr[i] == quote:
            if i + 1 < n and expr[i + 1] == quote:  # "" -> literal quote
                out.append(quote)
                i += 2
                continue
            return "".join(out), i + 1
        out.append(expr[i])
        i += 1
    return "".join(out), n  # unterminated: consume to end rather than raise


def _scan_bracket(expr: str, start: int) -> tuple[str, int]:
    """Scan a [bracketed] reference. ``]]`` is an escaped closing bracket."""
    i = start + 1
    n = len(expr)
    out: list[str] = []
    while i < n:
        if expr[i] == "]":
            if i + 1 < n and expr[i + 1] == "]":
                out.append("]")
                i += 2
                continue
            return "".join(out), i + 1
        out.append(expr[i])
        i += 1
    return "".join(out), n


def _scan_number(expr: str, start: int) -> int:
    i, n = start, len(expr)
    seen_dot = seen_exp = False
    while i < n:
        c = expr[i]
        if c.isdigit():
            i += 1
        elif c == "." and not seen_dot and not seen_exp:
            seen_dot = True
            i += 1
        elif c in "eE" and not seen_exp and i + 1 < n and (
            expr[i + 1].isdigit() or (expr[i + 1] in "+-" and i + 2 < n and expr[i + 2].isdigit())
        ):
            seen_exp = True
            i += 2 if expr[i + 1] in "+-" else 1
        else:
            break
    return i


def _canonical_number(text: str) -> str:
    """Collapse equivalent numeric spellings: 1.0, 1.00 and 1e0 all become 1."""
    try:
        d = Decimal(text).normalize()
    except InvalidOperation:
        return text
    sign, digits, exponent = d.as_tuple()
    # Decimal.normalize turns 1000 into 1E+3; expand it back for stability.
    if isinstance(exponent, int) and exponent > 0:
        d = d.quantize(Decimal(1))
    out = format(d, "f")
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    return out or "0"


def canonicalise(expr: str) -> str:
    """Reduce a DAX expression to its canonical form.

    Formatting, comments, casing and table-qualifier quoting are erased.
    Anything that changes what the expression computes is preserved.
    """
    tokens = [t for t in tokenize(expr) if t.kind not in (Kind.WS, Kind.COMMENT)]
    parts: list[str] = []

    for idx, tok in enumerate(tokens):
        nxt = tokens[idx + 1] if idx + 1 < len(tokens) else None

        if tok.kind is Kind.STRING:
            # Preserved exactly: this is data, not an object name.
            parts.append('"' + tok.value.replace('"', '""') + '"')

        elif tok.kind is Kind.BRACKET_REF:
            parts.append("[" + tok.value.strip().casefold() + "]")

        elif tok.kind is Kind.QUOTED_IDENT:
            parts.append("'" + tok.value.strip().casefold() + "'")

        elif tok.kind is Kind.IDENT:
            # A bare word directly before a [ref] is a table qualifier.
            # Always emit it quoted so Sales[Amount] and 'Sales'[Amount] agree.
            if nxt is not None and nxt.kind is Kind.BRACKET_REF:
                parts.append("'" + tok.value.casefold() + "'")
            else:
                parts.append(tok.value.casefold())

        elif tok.kind is Kind.NUMBER:
            parts.append(_canonical_number(tok.value))

        else:
            parts.append(tok.value)

    # Single-space join keeps the result unambiguous and trivially deterministic.
    return " ".join(parts)


@dataclass(frozen=True)
class References:
    """Objects an expression depends on.

    ``columns`` holds qualified ``(table, column)`` pairs. ``unqualified`` holds
    bare ``[Name]`` references, which DAX allows for both measures and columns in
    the current table -- resolving which is which needs the full model, so that
    is left to the graph builder.
    """
    columns: frozenset[tuple[str, str]]
    unqualified: frozenset[str]


def extract_references(expr: str) -> References:
    """Find the columns and measures an expression reads."""
    tokens = [t for t in tokenize(expr) if t.kind not in (Kind.WS, Kind.COMMENT)]
    columns: set[tuple[str, str]] = set()
    unqualified: set[str] = set()

    for idx, tok in enumerate(tokens):
        if tok.kind is not Kind.BRACKET_REF:
            continue
        prev = tokens[idx - 1] if idx else None

        # 'Calendar'[Date].[Month] is a date-hierarchy level, not an object in
        # its own right -- the real dependency is the base column, which the
        # preceding reference already recorded. The level still matters to the
        # fingerprint (.[Month] and .[Year] differ), so canonicalise keeps it;
        # only dependency extraction skips it.
        if (
            prev is not None
            and prev.kind is Kind.OP
            and prev.value == "."
            and idx >= 2
            and tokens[idx - 2].kind is Kind.BRACKET_REF
        ):
            continue

        if prev is not None and prev.kind in (Kind.IDENT, Kind.QUOTED_IDENT):
            columns.add((prev.value.strip(), tok.value.strip()))
        else:
            unqualified.add(tok.value.strip())

    return References(frozenset(columns), frozenset(unqualified))

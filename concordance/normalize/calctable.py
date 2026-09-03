"""What a DAX-calculated table contains, read from the expression that builds it.

A calculated table has no stored columns to enumerate -- its rows and its
columns are both produced by a DAX expression at refresh time. Power BI records
the expression and nothing else, so a reader that only enumerates stored columns
sees the table as empty, and a reader that only enumerates stored *tables* does
not see it at all.

Microsoft's Store Sales sample is exactly this case, and the cost of missing it
was not subtle: the model's `Date` table -- the one its own field descriptions
tell report authors to use -- vanished, taking both of its named drill-down
hierarchies with it, and the eight columns those hierarchies name were then
reported as unresolved references. Eight confident complaints about a model that
has the thing.

What is read here is only what the expression states in so many words: the
literal names an `ADDCOLUMNS` or `SELECTCOLUMNS` assigns, and the column
`CALENDAR` is defined to return. Nothing is evaluated and nothing is inferred
from data.
"""

from __future__ import annotations

from concordance.normalize.dax import Kind, Token, tokenize

#: Functions whose argument list assigns names to columns. The pairing rule
#: below is shared by all of them, which is why one set covers the lot.
_NAMING = frozenset({"ADDCOLUMNS", "SELECTCOLUMNS", "ROW", "SUMMARIZECOLUMNS"})

#: `CALENDAR` and `CALENDARAUTO` are defined to return a single column, named
#: `Date`. That is the function's contract rather than a guess about this model,
#: and it is how a date table gets the column every hierarchy on it drills to.
_CALENDAR = frozenset({"CALENDAR", "CALENDARAUTO"})
_CALENDAR_COLUMN = "Date"


def _significant(tokens: list[Token]) -> list[Token]:
    return [t for t in tokens if t.kind not in (Kind.WS, Kind.COMMENT)]


def _arguments(tokens: list[Token], opening: int) -> list[list[Token]]:
    """Split one call's arguments, given the index of its `(`.

    Commas inside nested calls belong to those calls; only a comma at the
    call's own depth separates its arguments.
    """
    args: list[list[Token]] = []
    current: list[Token] = []
    depth = 0
    for token in tokens[opening:]:
        if token.kind is Kind.OP and token.raw in "([{":
            depth += 1
            if depth == 1:
                continue
        elif token.kind is Kind.OP and token.raw in ")]}":
            depth -= 1
            if depth == 0:
                args.append(current)
                return args
        elif token.kind is Kind.OP and token.raw == "," and depth == 1:
            args.append(current)
            current = []
            continue
        current.append(token)
    # Unbalanced -- return what was read rather than raising. A malformed
    # expression should cost the column names, not the whole model.
    args.append(current)
    return args


def added_columns(expression: str) -> list[tuple[str, str]]:
    """Every column the expression names, as (name, the DAX behind it).

    The rule is deliberately one rule rather than one per function: inside a
    naming call, an argument that is a bare string literal names the argument
    that follows it. That is true of `ADDCOLUMNS`, `SELECTCOLUMNS` and `ROW`
    alike, and it steps over `SUMMARIZECOLUMNS`' group-by arguments without
    needing to know which position they sit in, because a column reference is
    never a bare string.

    Names are returned in the order they appear, deduplicated on first sight, so
    a table that assigns the same name twice is reported once rather than twice.
    """
    tokens = _significant(tokenize(expression))
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    for index, token in enumerate(tokens):
        if token.kind is not Kind.IDENT or token.raw.upper() not in _NAMING:
            continue
        following = tokens[index + 1] if index + 1 < len(tokens) else None
        if following is None or following.kind is not Kind.OP or following.raw != "(":
            continue

        args = _arguments(tokens, index + 1)
        position = 0
        while position < len(args):
            argument = args[position]
            if len(argument) == 1 and argument[0].kind is Kind.STRING:
                name = argument[0].value.strip('"').strip()
                body = args[position + 1] if position + 1 < len(args) else []
                if name and name.casefold() not in seen:
                    seen.add(name.casefold())
                    found.append((name, "".join(t.raw for t in body).strip()))
                position += 2
                continue
            position += 1

    return found


def calendar_column(expression: str) -> str | None:
    """`Date`, when the expression is built on `CALENDAR` or `CALENDARAUTO`."""
    for token in _significant(tokenize(expression)):
        if token.kind is Kind.IDENT and token.raw.upper() in _CALENDAR:
            return _CALENDAR_COLUMN
    return None


def column_names(expression: str) -> list[tuple[str, str | None]]:
    """Every column this expression states, as (name, expression or None).

    The calendar column comes first because it is the table's grain -- the thing
    every other column is derived from -- and `None` for its expression is the
    honest answer: `CALENDAR` states that the column exists and what it is
    called, not a formula for each row of it.
    """
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()

    base = calendar_column(expression)
    if base:
        seen.add(base.casefold())
        out.append((base, None))

    for name, body in added_columns(expression):
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        out.append((name, body or None))
    return out

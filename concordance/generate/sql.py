"""Translate DAX measures into runnable SQL at a stated grain.

Why a grain has to be named
---------------------------
A DAX measure has no single SQL equivalent, and that is not a gap in this
module -- it is a property of the language. ``[Batch Yield %]`` is one number
on a page sliced by site and a different number sliced by month, from
identical DAX, because DAX evaluates against a *filter context* supplied by
whatever is displaying it. SQL has no such notion.

Naming the grain is what closes that gap: ``GROUP BY`` *is* the filter
context, written down. So this module never claims to translate a measure. It
translates a measure **at a grain**, and the grain travels with the SQL.

What it refuses to do
---------------------
Some behaviours stay undecidable even once the grain is fixed -- time
intelligence shifts the context rather than reading it, ranking depends on the
set being ranked, ``USERELATIONSHIP`` swaps the join graph underneath. For
those this returns no SQL and says which construct stopped it, rather than
emitting something that parses and quietly computes the wrong number. A wrong
query is worse than an absent one: the absent one gets asked about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

import networkx as nx

from concordance.normalize.dax import Kind, Token, tokenize

# -- what the translator understands -------------------------------------------

#: DAX aggregate -> SQL aggregate. Only functions whose SQL equivalent is exact
#: are listed; anything absent is reported rather than guessed at.
_AGGREGATES: dict[str, str] = {
    "SUM": "SUM",
    "AVERAGE": "AVG",
    "MIN": "MIN",
    "MAX": "MAX",
    "COUNT": "COUNT",
    "COUNTA": "COUNT",
    "DISTINCTCOUNT": "COUNT",  # rendered with DISTINCT
}

#: Constructs that remain undecidable after the grain is fixed, and the reason.
#: Kept as data so the message a reader sees is the same one the tests assert.
_BLOCKERS: dict[str, str] = {
    "PREVIOUSMONTH": "shifts the date filter context rather than reading it",
    "PREVIOUSYEAR": "shifts the date filter context rather than reading it",
    "PREVIOUSQUARTER": "shifts the date filter context rather than reading it",
    "PREVIOUSDAY": "shifts the date filter context rather than reading it",
    "SAMEPERIODLASTYEAR": "shifts the date filter context rather than reading it",
    "DATEADD": "shifts the date filter context rather than reading it",
    "PARALLELPERIOD": "shifts the date filter context rather than reading it",
    "TOTALYTD": "accumulates over a date range the query does not fix",
    "TOTALQTD": "accumulates over a date range the query does not fix",
    "TOTALMTD": "accumulates over a date range the query does not fix",
    "DATESYTD": "accumulates over a date range the query does not fix",
    "DATESBETWEEN": "accumulates over a date range the query does not fix",
    "DATESINPERIOD": "accumulates over a date range the query does not fix",
    "RANKX": "ranks over a set the surrounding visual chooses, not the query",
    "TOPN": "ranks over a set the surrounding visual chooses, not the query",
    "ALL": "removes filter context, so the result depends on what was filtered",
    "ALLEXCEPT": "removes filter context, so the result depends on what was filtered",
    "ALLSELECTED": "removes filter context, so the result depends on what was filtered",
    "REMOVEFILTERS": "removes filter context, so the result depends on what was filtered",
    "USERELATIONSHIP": "evaluates against a different join than the model's active one",
    "CROSSFILTER": "changes the direction filters travel between tables",
    "ISINSCOPE": "reports differently depending on the level being viewed",
    "HASONEVALUE": "reports differently depending on the level being viewed",
    "SELECTEDVALUE": "reports differently depending on the level being viewed",
    "ISFILTERED": "reports differently depending on the level being viewed",
    "EARLIER": "refers to an outer row context with no SQL counterpart",
}


class Status(Enum):
    """How far the translation got."""

    #: Every construct mapped onto an exact SQL equivalent.
    EXACT = "exact"
    #: A construct in this measure has no SQL equivalent at any grain.
    BLOCKED = "blocked"
    #: The expression uses something this translator does not yet read.
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Translation:
    """One measure, rendered at one grain."""

    measure: str
    grain: tuple[str, ...]
    status: Status
    sql: str = ""
    #: Why there is no SQL, in the reader's terms. Empty when status is EXACT.
    reason: str = ""
    #: The construct that stopped it, for the reader who wants the specific name.
    blocked_by: str = ""
    reads_tables: frozenset[str] = field(default_factory=frozenset)
    reads_columns: frozenset[tuple[str, str]] = field(default_factory=frozenset)

    @property
    def translated(self) -> bool:
        return self.status is Status.EXACT


# -- a small expression tree over the existing lexer ---------------------------
#
# Only the shapes this module can compile are given nodes. Anything else is
# caught while parsing and reported by name, which is why an unknown function
# produces a message rather than a partial query.


@dataclass(frozen=True)
class Num:
    text: str


@dataclass(frozen=True)
class Str:
    text: str


@dataclass(frozen=True)
class Bool:
    value: bool


@dataclass(frozen=True)
class ColumnRef:
    table: str
    column: str
    #: A hierarchy level addressed off the column, as in ``[Date].[Month]``.
    #: Carried rather than discarded so the compiler can refuse a level it
    #: cannot express, instead of silently returning the whole date where the
    #: model asked for the month.
    level: str = ""


@dataclass(frozen=True)
class MeasureRef:
    name: str


@dataclass(frozen=True)
class TableRef:
    name: str


@dataclass(frozen=True)
class VarRef:
    name: str


@dataclass(frozen=True)
class Let:
    """``VAR a = ... RETURN body``. Bindings are substituted, not emitted as
    SQL locals: a DAX variable is evaluated once in the filter context where it
    was declared, which inlining reproduces and a SQL alias would not."""

    bindings: tuple[tuple[str, object], ...]
    body: object


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple[object, ...]


@dataclass(frozen=True)
class Binary:
    op: str
    left: object
    right: object


class Unsupported(Exception):
    """Raised with the construct's own name, so the reason can quote it."""

    def __init__(self, construct: str, why: str) -> None:
        super().__init__(why)
        self.construct = construct
        self.why = why


class Blocked(Unsupported):
    """A refusal that is a fact about the DAX, not a gap in this translator.

    The distinction is the whole honesty of this module, and it was previously
    carried only by a name lookup: a construct whose name appears in
    ``_BLOCKERS`` is blocked, anything else is a gap. That works for
    ``ALL`` and ``PREVIOUSMONTH``, and cannot work at all for a refusal whose
    subject is not a function -- a measure that reads no column is a constant,
    and its "construct" is the measure's own name, which no list can enumerate.

    Raising this says so directly. It matters because the two read completely
    differently to someone deciding whether to wait for a fix: a gap is a
    promise, and a block is an answer.
    """


class Parser:
    """Recursive descent over the DAX token stream.

    Deliberately small: it accepts the grammar this module can compile and
    raises :class:`Unsupported` on everything else, naming what it saw. Parsing
    all of DAX and failing later would report the failure further from its
    cause.
    """

    def __init__(self, tokens: list[Token]) -> None:
        self.toks = [t for t in tokens if t.kind not in (Kind.WS, Kind.COMMENT)]
        self.pos = 0
        #: Names bound by VAR in the enclosing scope, so a later bare use of one
        #: reads as a variable rather than as a table.
        self.vars: set[str] = set()

    # -- cursor ------------------------------------------------------------

    def peek(self) -> Token | None:
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def next(self) -> Token:
        tok = self.toks[self.pos]
        self.pos += 1
        return tok

    def at_op(self, *values: str) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind is Kind.OP and tok.value in values

    def eat_op(self, value: str) -> None:
        if not self.at_op(value):
            got = self.peek()
            raise Unsupported(got.raw if got else "end of expression",
                              f"expected {value!r}")
        self.next()

    # -- grammar -----------------------------------------------------------

    def parse(self) -> object:
        node = self.var_block()
        if self.peek() is not None:
            raise Unsupported(self.peek().raw, "trailing input this parser cannot read")
        return node

    def var_block(self) -> object:
        """``VAR name = expr ... RETURN body``, or a plain expression."""
        bindings: list[tuple[str, object]] = []
        while self._at_keyword("VAR"):
            self.next()
            name_tok = self.peek()
            if name_tok is None or name_tok.kind is not Kind.IDENT:
                raise Unsupported("VAR", "must be followed by a name")
            name = self.next().value
            self.eat_op("=")
            bindings.append((name, self.expression()))
            self.vars.add(name.casefold())

        if not bindings:
            return self.expression()

        if not self._at_keyword("RETURN"):
            raise Unsupported("VAR", "block has no RETURN")
        self.next()
        return Let(tuple(bindings), self.expression())

    def _at_keyword(self, word: str) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind is Kind.IDENT and tok.value.upper() == word

    #: The lexer emits these whole, so they are matched whole. Pairing two
    #: single characters here would silently never fire for ">=".
    _COMPARISONS = ("=", "<>", "<=", ">=", "<", ">")

    def expression(self) -> object:
        """Comparison is lowest precedence, then IN, then arithmetic."""
        node = self.additive()
        while True:
            if self.at_op(*self._COMPARISONS):
                op = self.next().value
                node = Binary(op, node, self.additive())
            elif self._at_keyword("IN"):
                self.next()
                node = Call("IN", (node, self.value_set()))
            else:
                return node

    def value_set(self) -> object:
        """DAX writes a literal set as ``{ a, b, c }``."""
        if not self.at_op("{"):
            raise Unsupported("IN", "is only read with a literal { ... } set")
        self.next()
        items: list[object] = []
        if not self.at_op("}"):
            items.append(self.expression())
            while self.at_op(","):
                self.next()
                items.append(self.expression())
        if not self.at_op("}"):
            raise Unsupported("IN", "set is not closed")
        self.next()
        return Call("__SET__", tuple(items))

    def additive(self) -> object:
        node = self.multiplicative()
        while self.at_op("+", "-", "&"):
            op = self.next().value
            node = Binary(op, node, self.multiplicative())
        return node

    def multiplicative(self) -> object:
        node = self.unary()
        while self.at_op("*", "/"):
            op = self.next().value
            node = Binary(op, node, self.unary())
        return node

    def unary(self) -> object:
        if self.at_op("-"):
            self.next()
            return Binary("-", Num("0"), self.unary())
        if self._at_keyword("NOT"):
            self.next()
            return Call("NOT", (self.unary(),))
        return self.primary()

    def primary(self) -> object:
        tok = self.peek()
        if tok is None:
            raise Unsupported("end of expression", "expression ended early")

        if tok.kind is Kind.NUMBER:
            return Num(self.next().value)

        if tok.kind is Kind.STRING:
            return Str(self.next().value)

        if tok.kind is Kind.BRACKET_REF:
            # A bare [Name] is a measure reference in every model this reads;
            # a column would be qualified by its table.
            return MeasureRef(self.next().value)

        if tok.kind is Kind.QUOTED_IDENT:
            name = self.next().value
            return self._after_table_name(name)

        if tok.kind is Kind.IDENT:
            name = self.next().value
            if self.at_op("("):
                return self._call(name)
            return self._after_table_name(name)

        if tok.kind is Kind.OP and tok.value == "(":
            self.next()
            inner = self.expression()
            self.eat_op(")")
            return inner

        raise Unsupported(tok.raw, "not something this translator reads")

    def _after_table_name(self, name: str) -> object:
        """A table name, optionally followed by [Column] and a hierarchy level.

        The trailing ``.[Month]`` is Power BI's auto date hierarchy:
        ``'Calendar'[Date].[Month]`` addresses a level of the hierarchy Power BI
        generates for every date column. It is extremely common in models people
        actually build -- and it was a parse error here, which reported "expected
        ')'" for four measures in a real Microsoft sample. That is the worst kind
        of refusal: it blames the DAX for a gap in the reader.

        The level is consumed and dropped rather than translated. Every one of
        these seen so far sits inside ``ALL()``, which is refused for a real
        reason, and dropping the level lets that real reason be the one reported.
        A level that ever reaches the compiler on its own would need a date part
        in the SQL, and would be wrong to silently ignore -- so it is recorded on
        the reference rather than thrown away.
        """
        if name.casefold() in self.vars:
            return VarRef(name)
        tok = self.peek()
        if tok is not None and tok.kind is Kind.BRACKET_REF:
            column = self.next().value
            level = ""
            while self.at_op("."):
                self.next()
                after = self.peek()
                if after is None or after.kind is not Kind.BRACKET_REF:
                    break
                level = self.next().value
            return ColumnRef(name, column, level)
        return TableRef(name)

    def _call(self, name: str) -> object:
        upper = name.upper()
        self.eat_op("(")
        args: list[object] = []
        if not self.at_op(")"):
            args.append(self.expression())
            while self.at_op(","):
                self.next()
                # CALCULATE(expr, , DESC) and similar leave holes; a hole is
                # only ever an omitted optional, so it is skipped rather than
                # parsed as an expression.
                if self.at_op(",") or self.at_op(")"):
                    continue
                args.append(self.expression())
        self.eat_op(")")

        if upper in _BLOCKERS:
            raise Unsupported(upper, _BLOCKERS[upper])
        return Call(upper, tuple(args))


# -- compiling the tree into SQL -----------------------------------------------


@dataclass
class _Fragment:
    """A compiled expression, plus what it needed to compile.

    ``filters`` are collected rather than inlined because ``CALCULATE`` applies
    them to its inner aggregate only. Rendering them as ``FILTER (WHERE ...)``
    keeps two differently-filtered aggregates in one SELECT correct, which a
    shared WHERE clause could not.
    """

    sql: str
    tables: set[str] = field(default_factory=set)
    columns: set[tuple[str, str]] = field(default_factory=set)


class Compiler:
    """Compile one measure against one model, at one grain."""

    def __init__(self, model, quote: str = '"') -> None:
        self.model = model
        self.quote = quote
        self.measures = {m.name.casefold(): m for m in model.measures}
        self.tables = {t.name.casefold(): t.name for t in model.user_tables()}
        self.columns = {
            (c.table.casefold(), c.name.casefold()): (c.table, c.name)
            for c in model.columns
        }
        # A calculated column has no storage in the source, so it is inlined
        # rather than selected: the query recomputes it from the columns it was
        # derived from, which is what makes the result runnable against the
        # source tables rather than only against a refreshed model.
        self.calculated = {
            (c.table.casefold(), c.name.casefold()): (getattr(c, "expression", "") or "")
            for c in model.columns
            if (getattr(c, "expression", "") or "").strip()
        }
        self._joins = self._join_graph()
        self._seen: list[str] = []   # measure inline stack, to catch cycles
        self._vars: dict[str, object] = {}  # VAR bindings currently in scope

    # -- identifiers -------------------------------------------------------

    def q(self, name: str) -> str:
        return f"{self.quote}{name}{self.quote}"

    def col(self, table: str, column: str) -> str:
        return f"{self.q(table)}.{self.q(column)}"

    # -- joins -------------------------------------------------------------

    def _join_graph(self) -> nx.Graph:
        """Active relationships only.

        An inactive relationship is not part of the model's default filter
        flow, so joining along it would answer a question nobody asked. The
        measures that do use one are stopped earlier, by USERELATIONSHIP.
        """
        g = nx.Graph()
        for t in self.model.user_tables():
            g.add_node(t.name)
        for rel in self.model.relationships:
            if not rel.is_active:
                continue
            g.add_edge(
                rel.from_table,
                rel.to_table,
                left=(rel.from_table, rel.from_column),
                right=(rel.to_table, rel.to_column),
            )
        return g

    def join_path(self, start: str, end: str) -> list[tuple[str, str, str, str]]:
        """Table hops from ``start`` to ``end``, as (lt, lc, rt, rc) tuples."""
        if start == end:
            return []
        try:
            nodes = nx.shortest_path(self._joins, start, end)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            raise Unsupported(
                f"{start} -> {end}",
                f"no active relationship path joins {start} to {end}",
            ) from None
        hops = []
        for a, b in zip(nodes, nodes[1:]):
            edge = self._joins.edges[a, b]
            (lt, lc), (rt, rc) = edge["left"], edge["right"]
            hops.append((lt, lc, rt, rc))
        return hops

    # -- expression compilation -------------------------------------------

    def compile(self, node: object, where: tuple[str, ...] = ()) -> _Fragment:
        if isinstance(node, Num):
            return _Fragment(node.text)

        if isinstance(node, Str):
            escaped = node.text.replace("'", "''")
            return _Fragment(f"'{escaped}'")

        if isinstance(node, Bool):
            return _Fragment("TRUE" if node.value else "FALSE")

        if isinstance(node, ColumnRef):
            return self._column(node)

        if isinstance(node, MeasureRef):
            return self._measure(node, where)

        if isinstance(node, TableRef):
            raise Unsupported(node.name, "a bare table is not a value")

        if isinstance(node, Let):
            return self._let(node, where)

        if isinstance(node, VarRef):
            bound = self._vars.get(node.name.casefold())
            if bound is None:
                raise Unsupported(node.name, "is used before it is defined")
            return self.compile(bound, where)

        if isinstance(node, Binary):
            return self._binary(node, where)

        if isinstance(node, Call):
            return self._call(node, where)

        raise Unsupported(type(node).__name__, "not something this translator compiles")

    def _column(self, node: ColumnRef) -> _Fragment:
        if node.level:
            # Reached only when a level is used outside the ALL()/ALLSELECTED()
            # calls that account for every one seen so far. Refused rather than
            # flattened: returning the date where the model asked for its month
            # is a wrong number, which is the one thing worse than no number.
            raise Blocked(
                f"{node.table}[{node.column}].[{node.level}]",
                "addresses a level of Power BI's automatic date hierarchy, "
                "which has no column in the warehouse to read",
            )
        key = (node.table.casefold(), node.column.casefold())
        if key in self.calculated:
            label = f"{node.table}[{node.column}]"
            if label in self._seen:
                raise Unsupported(label, "is a calculated column defined in terms of itself")
            self._seen.append(label)
            try:
                tree = Parser(tokenize(self.calculated[key])).parse()
                inner = self.compile(tree, ())
                # Parenthesised so it composes inside an aggregate or a comparison.
                return _Fragment(f"({inner.sql})", inner.tables, inner.columns)
            finally:
                self._seen.pop()
        if key not in self.columns:
            # `'% Return Rate'[% Return Rate Value]` is a *measure*, written with
            # its table in front. Valid DAX, and indistinguishable from a column
            # reference until the lookup misses -- so the miss is where it is
            # handled. Without this, two measures in a real Microsoft sample were
            # refused as "not a column in this model", which is both wrong and
            # unactionable: the thing exists, it is just not a column.
            qualified = self.measures.get(node.column.casefold())
            if qualified is not None:
                return self._measure(MeasureRef(node.column), ())
            raise Unsupported(f"{node.table}[{node.column}]", "is not a column in this model")
        table, column = self.columns[key]
        return _Fragment(self.col(table, column), {table}, {(table, column)})

    def _measure(self, node: MeasureRef, where: tuple[str, ...]) -> _Fragment:
        measure = self.measures.get(node.name.casefold())
        if measure is None:
            raise Unsupported(f"[{node.name}]", "is not a measure in this model")
        if measure.name in self._seen:
            raise Unsupported(f"[{node.name}]", "refers to itself through a cycle")

        self._seen.append(measure.name)
        try:
            tree = Parser(tokenize(measure.expression)).parse()
            return self.compile(tree, where)
        finally:
            self._seen.pop()

    def _let(self, node: Let, where: tuple[str, ...]) -> _Fragment:
        saved = dict(self._vars)
        try:
            for name, value in node.bindings:
                self._vars[name.casefold()] = value
            return self.compile(node.body, where)
        finally:
            self._vars = saved

    def _conditional(self, node: Call, where: tuple[str, ...]) -> _Fragment:
        """IF and SWITCH become CASE.

        Sound at a grain because every branch is evaluated per group: the
        aggregates inside a branch are grouped exactly as the surrounding
        query groups, so the comparison happens on the same row the value does.
        """
        tables: set[str] = set()
        cols: set[tuple[str, str]] = set()

        def add(frag: _Fragment) -> str:
            tables.update(frag.tables)
            cols.update(frag.columns)
            return frag.sql

        if node.name == "IF":
            if len(node.args) < 2:
                raise Unsupported("IF", "needs a condition and a result")
            cond = add(self.compile(node.args[0], where))
            then = add(self.compile(node.args[1], where))
            parts = [f"CASE WHEN {cond} THEN {then}"]
            if len(node.args) >= 3:
                parts.append(f"ELSE {add(self.compile(node.args[2], where))}")
            parts.append("END")
            return _Fragment(" ".join(parts), tables, cols)

        # SWITCH(TRUE(), cond, value, cond, value, ..., default)
        if not node.args:
            raise Unsupported("SWITCH", "needs an expression to switch on")
        head = node.args[0]
        if not (isinstance(head, Call) and head.name == "TRUE"):
            raise Unsupported(
                "SWITCH",
                "is only read in its SWITCH(TRUE(), ...) form, where each branch "
                "is a condition",
            )
        rest = list(node.args[1:])
        parts = ["CASE"]
        while len(rest) >= 2:
            cond = add(self.compile(rest.pop(0), where))
            val = add(self.compile(rest.pop(0), where))
            parts.append(f"WHEN {cond} THEN {val}")
        if rest:
            parts.append(f"ELSE {add(self.compile(rest.pop(0), where))}")
        parts.append("END")
        return _Fragment(" ".join(parts), tables, cols)

    def _binary(self, node: Binary, where: tuple[str, ...]) -> _Fragment:
        left = self.compile(node.left, where)
        right = self.compile(node.right, where)
        op = "||" if node.op == "&" else node.op
        return _Fragment(
            f"({left.sql} {op} {right.sql})",
            left.tables | right.tables,
            left.columns | right.columns,
        )

    def _call(self, node: Call, where: tuple[str, ...]) -> _Fragment:
        name = node.name

        if name == "CALCULATE":
            return self._calculate(node, where)
        if name == "DIVIDE":
            return self._divide(node, where)
        if name == "COUNTROWS":
            return self._countrows(node, where)
        if name in _AGGREGATES:
            return self._aggregate(node, where)
        if name in ("TRUE", "FALSE"):
            return _Fragment(name)
        if name == "BLANK":
            return _Fragment("NULL")
        if name == "DATEDIFF":
            return self._datediff(node, where)
        if name == "TODAY":
            return _Fragment("CURRENT_DATE")
        if name == "NOT":
            inner = self.compile(node.args[0], where)
            return _Fragment(f"(NOT {inner.sql})", inner.tables, inner.columns)
        if name == "IN":
            return self._in(node, where)
        if name == "COALESCE":
            return self._passthrough("COALESCE", node, where)
        # Scalar functions that mean the same thing in DAX and in SQL, and are
        # spelled the same. Added after running the translator over real
        # Microsoft sample models rather than only the three written for this
        # project: CONCATENATE alone accounted for seven refusals reading "not a
        # function this translator reads yet", which is a gap in the reader
        # dressed up as a property of the DAX.
        if name in ("ROUND", "ABS", "CEILING", "FLOOR", "SQRT", "POWER", "EXP", "LN", "LOG10"):
            return self._passthrough(name, node, where)
        if name == "CONCATENATE":
            # DAX takes exactly two arguments; SQL's CONCAT takes any number, so
            # the arity is checked here rather than left to the warehouse.
            if len(node.args) != 2:
                raise Unsupported("CONCATENATE", "takes exactly two arguments in DAX")
            return self._passthrough("CONCAT", node, where)
        if name == "VALUE":
            # Text to number. CAST rather than a warehouse-specific TO_NUMBER,
            # so sqlglot can transpile it to each dialect's own spelling.
            if len(node.args) != 1:
                raise Unsupported("VALUE", "takes one argument")
            arg = self.compile(node.args[0], where)
            return _Fragment(
                f"CAST({arg.sql} AS DOUBLE)", arg.tables, arg.columns
            )
        if name == "MEDIAN":
            if len(node.args) != 1:
                raise Unsupported("MEDIAN", "takes one column")
            arg = self.compile(node.args[0], where)
            # Spelled as the ordered-set form, which is the ANSI spelling and
            # transpiles cleanly; MEDIAN() itself is not portable.
            return _Fragment(
                self._filtered(
                    f"PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {arg.sql})", where
                ),
                arg.tables,
                arg.columns,
            )
        if name == "FORMAT":
            # Blocked rather than unsupported: FORMAT turns a number into a
            # display string using Power BI's own format-string vocabulary
            # ("#,0.0%,,", "dd mmm yyyy"), which has no portable SQL equivalent
            # and is a presentation choice rather than a calculation. The number
            # underneath it is what a warehouse would compute.
            raise Blocked(
                "FORMAT",
                "renders a number as display text using Power BI's format "
                "strings, which are a presentation concern with no warehouse "
                "equivalent -- translate the measure it wraps instead",
            )
        if name in ("IF", "SWITCH"):
            return self._conditional(node, where)
        if name == "ISBLANK":
            if len(node.args) != 1:
                raise Unsupported("ISBLANK", "takes one argument")
            inner = self.compile(node.args[0], where)
            return _Fragment(f"({inner.sql} IS NULL)", inner.tables, inner.columns)

        raise Unsupported(name, "is not a function this translator reads yet")

    _DATE_PARTS = {"DAY", "MONTH", "YEAR", "QUARTER", "WEEK", "HOUR", "MINUTE", "SECOND"}

    def _datediff(self, node: Call, where: tuple[str, ...]) -> _Fragment:
        """DAX's DATEDIFF(start, end, PART) -> DATE_DIFF('part', start, end).

        The unit arrives as a bare word rather than a string, so it is read off
        the parse tree as a table reference and validated here; an unrecognised
        unit is refused rather than passed through into the query.
        """
        if len(node.args) != 3:
            raise Unsupported("DATEDIFF", "takes a start, an end and a unit")
        unit_node = node.args[2]
        unit = getattr(unit_node, "name", getattr(unit_node, "text", "")).upper()
        if unit not in self._DATE_PARTS:
            raise Unsupported("DATEDIFF", f"was given an unrecognised unit {unit!r}")
        start = self.compile(node.args[0], where)
        end = self.compile(node.args[1], where)
        return _Fragment(
            f"DATE_DIFF('{unit.lower()}', {start.sql}, {end.sql})",
            start.tables | end.tables,
            start.columns | end.columns,
        )

    def _in(self, node: Call, where: tuple[str, ...]) -> _Fragment:
        """``col IN { "a", "b" }`` -> ``col IN ('a', 'b')``."""
        left = self.compile(node.args[0], where)
        members = node.args[1]
        if not (isinstance(members, Call) and members.name == "__SET__"):
            raise Unsupported("IN", "is only read with a literal set")
        frags = [self.compile(a, where) for a in members.args]
        if not frags:
            raise Unsupported("IN", "was given an empty set")
        values = ", ".join(f.sql for f in frags)
        cols = set(left.columns).union(*[f.columns for f in frags])
        tabs = set(left.tables).union(*[f.tables for f in frags])
        return _Fragment(f"({left.sql} IN ({values}))", tabs, cols)

    def _passthrough(self, sql_name: str, node: Call, where: tuple[str, ...]) -> _Fragment:
        """A function whose SQL spelling and meaning match the DAX one.

        Argument order and count are DAX's; nothing is reordered or defaulted.
        Anything needing either -- DIVIDE's third argument, DATEDIFF's unit --
        has its own branch rather than being squeezed through here.
        """
        frags = [self.compile(argument, where) for argument in node.args]
        return _Fragment(
            f"{sql_name}({', '.join(f.sql for f in frags)})",
            set().union(*[f.tables for f in frags]) if frags else set(),
            set().union(*[f.columns for f in frags]) if frags else set(),
        )

    def _filtered(self, inner: str, where: tuple[str, ...]) -> str:
        """Apply CALCULATE's filters to one aggregate, not the whole query."""
        if not where:
            return inner
        return f"{inner} FILTER (WHERE {' AND '.join(where)})"

    def _aggregate(self, node: Call, where: tuple[str, ...]) -> _Fragment:
        if len(node.args) != 1:
            raise Unsupported(node.name, "takes exactly one column")
        arg = self.compile(node.args[0], where)
        fn = _AGGREGATES[node.name]
        distinct = "DISTINCT " if node.name == "DISTINCTCOUNT" else ""
        return _Fragment(
            self._filtered(f"{fn}({distinct}{arg.sql})", where),
            arg.tables,
            arg.columns,
        )

    def _countrows(self, node: Call, where: tuple[str, ...]) -> _Fragment:
        if len(node.args) != 1 or not isinstance(node.args[0], TableRef):
            raise Unsupported("COUNTROWS", "takes a table name")
        name = node.args[0].name
        real = self.tables.get(name.casefold())
        if real is None:
            raise Unsupported(name, "is not a table in this model")
        # COUNT(*) rather than COUNT(col): counting rows must not depend on a
        # column being non-null.
        return _Fragment(self._filtered("COUNT(*)", where), {real}, set())

    def _divide(self, node: Call, where: tuple[str, ...]) -> _Fragment:
        if len(node.args) < 2:
            raise Unsupported("DIVIDE", "takes a numerator and a denominator")
        num = self.compile(node.args[0], where)
        den = self.compile(node.args[1], where)
        # NULLIF reproduces DAX's divide-by-zero guard exactly: DAX returns the
        # alternate result, and NULL propagates the same way here.
        expr = f"({num.sql} / NULLIF({den.sql}, 0))"
        if len(node.args) >= 3:
            alt = self.compile(node.args[2], where)
            if alt.sql != "NULL":
                expr = f"COALESCE({expr}, {alt.sql})"
        return _Fragment(expr, num.tables | den.tables, num.columns | den.columns)

    def _calculate(self, node: Call, where: tuple[str, ...]) -> _Fragment:
        if not node.args:
            raise Unsupported("CALCULATE", "takes an expression")
        extra: list[str] = []
        cols: set[tuple[str, str]] = set()
        tables: set[str] = set()
        for arg in node.args[1:]:
            comparison = isinstance(arg, Binary) and arg.op in (
                "=", "<>", "<", ">", "<=", ">=",
            )
            predicate = isinstance(arg, Call) and arg.name in ("NOT", "IN", "ISBLANK")
            if not (comparison or predicate):
                raise Unsupported(
                    "CALCULATE",
                    "only accepts column comparisons, IN, NOT and ISBLANK as filters here",
                )
            frag = self.compile(arg, ())
            extra.append(frag.sql)
            cols |= frag.columns
            tables |= frag.tables
        inner = self.compile(node.args[0], where + tuple(extra))
        return _Fragment(inner.sql, inner.tables | tables, inner.columns | cols)


# -- the public entry point ----------------------------------------------------


def _grain_columns(model, grain: tuple[str, ...]) -> list[tuple[str, str]]:
    """Parse ``Table[Column]`` grain strings against the model."""
    by_key = {
        (c.table.casefold(), c.name.casefold()): (c.table, c.name)
        for c in model.columns
    }
    out = []
    for item in grain:
        text = item.strip()
        if "[" not in text or not text.endswith("]"):
            raise Unsupported(item, "a grain must be written Table[Column]")
        table, column = text[:-1].split("[", 1)
        key = (table.strip().strip("'").casefold(), column.casefold())
        if key not in by_key:
            raise Unsupported(item, "is not a column in this model")
        out.append(by_key[key])
    return out


#: sqlglot's name for each platform this project connects to. Generation
#: happens once in DuckDB's dialect -- the one the bundled warehouse runs, so
#: the one the output can actually be tested against -- and is transpiled from
#: there, rather than maintaining a separate emitter per platform.
DIALECTS: dict[str, str] = {
    "duckdb": "duckdb",
    "snowflake": "snowflake",
    "databricks": "databricks",
    "redshift": "redshift",
    "athena": "trino",
}


def to_dialect(sql: str, dialect: str) -> str:
    """Re-render generated SQL for another warehouse.

    Returns the input unchanged if the dialect is unknown or sqlglot cannot
    read it back: an untranslated query that runs on one platform is a better
    outcome than a mangled one that runs nowhere.
    """
    target = DIALECTS.get(dialect.lower())
    if target is None or target == "duckdb":
        return sql
    try:
        import sqlglot

        out = sqlglot.transpile(sql, read="duckdb", write=target, pretty=True)
        return out[0] if out else sql
    except Exception:
        return sql


def translate(model, measure, grain: tuple[str, ...] = (), quote: str = '"') -> Translation:
    """Render one measure as SQL at ``grain``, or say why it cannot be.

    ``grain`` is a tuple of ``Table[Column]`` strings. An empty grain means the
    whole model -- a single row, which is the honest reading of a measure with
    no filter context applied.
    """
    compiler = Compiler(model, quote=quote)

    try:
        grain_cols = _grain_columns(model, grain)
        tree = Parser(tokenize(measure.expression)).parse()
        body = compiler.compile(tree)

        needed = set(body.tables) | {t for t, _ in grain_cols}
        if not needed:
            # Blocked, not unsupported. A measure that reads no column is a
            # constant -- a label, a tooltip, a hard-coded target -- and there
            # is nothing to select it FROM. That is a fact about the measure,
            # and filing it under "the translator has not learned this yet"
            # promises a fix that will never come.
            raise Blocked(
                measure.name,
                "is a constant: it reads no column, so there is no table to "
                "query and nothing a warehouse could compute",
            )

        # The base is whichever table the measure itself reads; the grain is
        # joined onto it, never the other way round. Starting from the grain
        # would change which rows survive a many-to-one join.
        base = sorted(body.tables)[0] if body.tables else sorted(needed)[0]

        joins: list[str] = []
        joined = {base}
        for table in sorted(needed - {base}):
            for lt, lc, rt, rc in compiler.join_path(base, table):
                target = rt if lt in joined else lt
                if target in joined:
                    continue
                joins.append(
                    f"JOIN {compiler.q(target)} "
                    f"ON {compiler.col(lt, lc)} = {compiler.col(rt, rc)}"
                )
                joined.add(target)

        select = [compiler.col(t, c) for t, c in grain_cols]
        select.append(f"{body.sql} AS {compiler.q(measure.name)}")

        lines = [f"SELECT {', '.join(select)}", f"FROM {compiler.q(base)}"]
        lines.extend(joins)
        if grain_cols:
            group = ", ".join(compiler.col(t, c) for t, c in grain_cols)
            lines.append(f"GROUP BY {group}")
            lines.append(f"ORDER BY {group}")

        return Translation(
            measure=measure.name,
            grain=grain,
            status=Status.EXACT,
            sql="\n".join(lines),
            reads_tables=frozenset(body.tables),
            reads_columns=frozenset(body.columns),
        )

    except Unsupported as stop:
        blocked = isinstance(stop, Blocked) or stop.construct.upper() in _BLOCKERS
        return Translation(
            measure=measure.name,
            grain=grain,
            status=Status.BLOCKED if blocked else Status.UNSUPPORTED,
            reason=f"{stop.construct} {stop.why}",
            blocked_by=stop.construct,
        )


def translate_all(model, grain: tuple[str, ...] = (), quote: str = '"') -> list[Translation]:
    """Every measure in the model, at one grain, in model order."""
    return [translate(model, m, grain, quote) for m in model.measures]

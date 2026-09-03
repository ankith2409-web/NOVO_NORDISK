"""Running a measure's translated SQL against the model's own data.

Every other part of this project reads definitions. This part runs them, and it
is the only place a number appears on screen that was not copied out of a file.

That is worth being careful about, so here is exactly where the figures come
from. A ``.pbix`` is not only a schema: it carries the rows the report was
built on -- 923,371 of them in Store Sales' fact table. Those rows go into an
in-memory DuckDB, and the SQL this project already translates each measure into
is executed against them. Nothing is estimated, sampled or inferred. If the
translation is right the figure is right, and the SQL that produced it is shown
beside it so a reader can check rather than trust.

Two consequences worth stating plainly, because both are visible on screen:

**A measure with no SQL has no value here.** Roughly one in ten refuses to
translate -- ``ALL`` discards the report's filters, so its answer depends on
what the reader clicked, and no single query stands for it. Those measures keep
their DAX and say why, exactly as they do everywhere else. A dashboard that
quietly printed a plausible number for them would be the one thing this project
exists not to do.

**The value is the number the query returned, formatted for magnitude only.**
Power BI displays ``0.4229`` as ``42.29%`` because the measure carries a format
string; that string is not in what this file's reader exposes, so reinterpreting
a ratio as a percentage here would be a guess dressed as a fact. The raw figure
is shown, and the page says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Tables above this many rows are still loaded in full. The constant exists to
#: be *not* used as a sampling limit: a partial sum is a wrong answer that looks
#: exactly like a right one, and no performance saving is worth that. It is here
#: only so the interface can warn that a first load will take a moment.
LARGE_MODEL_ROWS = 500_000


@dataclass(frozen=True)
class Value:
    """One measure, run."""

    measure: str
    table: str
    #: The figure the query returned. ``None`` when it could not be computed,
    #: in which case ``reason`` says why -- never a stand-in zero.
    value: float | None
    #: The query that produced it, so the number can be checked rather than
    #: trusted. Empty when the measure does not translate at all.
    sql: str = ""
    #: Why there is no figure. Empty when there is one.
    reason: str = ""

    @property
    def computed(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class Evaluation:
    """Every measure in a model, run against its own rows."""

    #: False when the source carries no data to run against -- a
    #: `.SemanticModel` folder is a schema with no rows behind it.
    available: bool
    #: Why not, when it is not. Shown to the reader rather than left blank.
    reason: str
    values: tuple[Value, ...] = ()
    rows_loaded: int = 0

    @property
    def computed(self) -> tuple[Value, ...]:
        return tuple(v for v in self.values if v.computed)

    def by_name(self) -> dict[str, Value]:
        return {v.measure: v for v in self.values}


def open_data(model) -> tuple[Any, int, str]:
    """Put the model's own rows into an in-memory DuckDB.

    The caller owns the connection and must close it. Kept open by the web
    layer for the life of the server, because loading a million rows takes
    three seconds and every chart on the dashboard is another query against
    the same rows.

    Returns the connection, how many rows went in, and a reason string that is
    non-empty only when nothing could be loaded. Failure here is reported, not
    raised: a model whose data cannot be read is still a model worth
    documenting, and the rest of the interface must keep working.
    """
    if model.source_type != "pbix":
        return None, 0, (
            f"A {model.source_type} source describes the model without carrying "
            "the rows behind it, so there is nothing here to run a query "
            "against. Open the same model as a .pbix and every figure below "
            "is computed from its own data."
        )

    try:
        import duckdb
        from pbixray import PBIXRay
    except ImportError as exc:  # pragma: no cover - depends on the install
        return None, 0, f"the query engine is not installed here: {exc}"

    try:
        raw = PBIXRay(model.source_path)
        connection = duckdb.connect()
        rows = 0
        for name in raw.tables:
            frame = raw.get_table(name)
            rows += len(frame)
            # Registered under a fixed placeholder and immediately materialised,
            # so a table whose name needs quoting -- "Fiscal calendar" -- does
            # not have to survive being turned into an identifier twice.
            connection.register("_concordance_source", frame)
            escaped = name.replace('"', '""')
            connection.execute(
                f'CREATE TABLE "{escaped}" AS SELECT * FROM _concordance_source'
            )
            connection.unregister("_concordance_source")
        return connection, rows, ""
    except Exception as exc:  # noqa: BLE001 - any reader failure is reportable
        return None, 0, f"the rows in this file could not be read: {exc}"


def _single(
    measure: str, table: str, sql: str, columns: list[str], rows: list
) -> Value:
    """The one figure a query stands for, or an honest account of why there isn't one.

    Two traps here, both found by running the queries rather than reading them.

    **The measure is not column zero.** A query at a grain selects the grain
    first -- `SELECT DATE_TRUNC('month', ...) AS "month", ... AS "Net Sales PM"`
    -- so reading position zero returns the month. Twelve of Sales & Returns'
    measures reported `2019-01-01` as their value, which is not a rounding error
    or a formatting quirk: it is a date where a percentage belongs, and it looks
    exactly as confident as a right answer. The column is found by the name the
    translator aliased it to.

    **A grouped result has no single figure.** A previous-month measure is
    meaningful at one row per month and at no other -- that is why it translates
    at all -- so there is no whole-model number to put on a card. Reporting the
    first month's would be picking one arbitrarily and presenting it as the
    total.
    """
    if not rows:
        return Value(
            measure=measure,
            table=table,
            value=None,
            sql=sql,
            reason="the query ran and returned no rows, so there is nothing to report",
        )

    if len(rows) > 1:
        return Value(
            measure=measure,
            table=table,
            value=None,
            sql=sql,
            reason=(
                "this measure is only meaningful one row at a time -- the query "
                "returns a figure per period, not one for the whole model -- so "
                "there is no single number to show. The query is here and runs."
            ),
        )

    wanted = measure.casefold()
    index = next(
        (at for at, name in enumerate(columns) if name.casefold() == wanted), None
    )
    if index is None:
        # Falls back only where there is no ambiguity to resolve. Guessing which
        # of several columns was meant is how a grain ends up on a card.
        if len(columns) != 1:
            return Value(
                measure=measure,
                table=table,
                value=None,
                sql=sql,
                reason=(
                    "the query returned several columns and none of them is named "
                    f"{measure!r}, so which one is the figure cannot be settled here"
                ),
            )
        index = 0

    figure = rows[0][index]
    if figure is None:
        return Value(
            measure=measure,
            table=table,
            value=None,
            sql=sql,
            reason="the query ran and the figure came back empty",
        )

    # `bool` is an `int` in Python and would otherwise render as 1 or 0.
    if isinstance(figure, bool) or not isinstance(figure, (int, float)):
        return Value(
            measure=measure,
            table=table,
            value=None,
            sql=sql,
            reason=(
                f"the query returned a {type(figure).__name__}, not a number, so "
                "there is no figure to put on a card"
            ),
        )

    return Value(measure=measure, table=table, value=float(figure), sql=sql)


def evaluate(model, grain: tuple[str, ...] = (), connection: Any = None) -> Evaluation:
    """Run every measure that translates, against the model's own rows.

    ``grain`` is passed through to the same translator the rest of the project
    uses, so the query run here is the query shown everywhere else for that
    measure. Two different SQL strings for one measure would make the figure
    unverifiable, which would defeat the point of showing it.

    A ``connection`` may be passed in by a caller that already loaded the rows
    -- the web layer does, because every chart it draws is another query
    against the same million -- in which case that caller keeps ownership of
    it. Opened here only when it was not, and then closed here.
    """
    from concordance.generate.sql import Status, translate_all

    borrowed = connection is not None
    rows = 0
    if not borrowed:
        connection, rows, reason = open_data(model)
        if connection is None:
            return Evaluation(available=False, reason=reason)

    try:
        values: list[Value] = []
        for result in translate_all(model, grain):
            measure = next(
                (m for m in model.measures if m.name == result.measure), None
            )
            table = measure.table if measure else ""

            if result.status is not Status.EXACT:
                values.append(
                    Value(
                        measure=result.measure,
                        table=table,
                        value=None,
                        reason=result.reason,
                    )
                )
                continue

            try:
                cursor = connection.execute(result.sql)
                columns = [column[0] for column in cursor.description or []]
                # Two, not all: enough to tell a single figure from a grouped
                # result without pulling a month-by-month table into memory to
                # discover it is one.
                returned = cursor.fetchmany(2)
            except Exception as exc:  # noqa: BLE001 - a bad query is a finding
                # Reported against the measure rather than swallowed. A query
                # this project generated that the engine rejects is a defect in
                # the translation, and hiding it would hide the defect.
                values.append(
                    Value(
                        measure=result.measure,
                        table=table,
                        value=None,
                        sql=result.sql,
                        reason=f"the generated query did not run: {exc}",
                    )
                )
                continue

            values.append(
                _single(result.measure, table, result.sql, columns, returned)
            )

        return Evaluation(
            available=True,
            reason="",
            values=tuple(values),
            rows_loaded=rows,
        )
    finally:
        if not borrowed:
            connection.close()

"""One measure, split by a dimension -- the numbers behind a chart.

A KPI card answers "what is the total". Every chart on a dashboard answers the
next question: *what is it made of*. Both come from the same place here -- the
model's own rows, queried with the SQL this project already translates each
measure into -- so a bar is as checkable as the figure on the card above it,
and the query that produced it is returned alongside.

Which dimensions get charted is decided by measuring the data, not by reading
column names. That distinction is the whole of this module's difficulty, and
Store Sales shows why on every count:

* `Item[Segment]` sounds like a dimension and holds 1,415 distinct values. A
  bar chart of 1,415 bars is not a chart.
* `District[DM_Pic_fl]` holds `http://farm6.staticflickr.com/...`, and
  `District[DMImage]` holds raw JPEG bytes. Both have nine distinct values, so
  cardinality alone waves them straight through; only looking at a value
  catches them.
* `Store[Chain]` and `Store[Store type]` are the two best charts in the file --
  clean two-way splits -- and the grain picker used elsewhere in this project
  offers neither, because `Store` sits in the middle of a snowflake and that
  picker deliberately only offers leaves.

So: candidates come from any table something points at, junk is rejected by
inspecting a value rather than trusting a name, and at most one column per
table is charted, which is what makes four charts show four different angles
instead of four views of the same district list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: A dimension needs at least this many distinct values to be worth drawing.
#: One bar is not a comparison; it is a number, and the card already showed it.
MIN_CLASSES = 2

#: And at most this many distinct values to be offered at all. Well above the
#: number of bars actually drawn, because a column with sixteen categories is a
#: good chart once its tail is folded, while one with fifteen hundred is not a
#: chart at any level of folding.
MAX_CLASSES = 30

#: Bars actually drawn. The rest are summed into one, so the total still adds
#: up -- a chart of the top eight that silently dropped the ninth would not.
MAX_SLICES = 10

#: Whole names or suffixes that mean "an identifier", never a substring: a
#: column called `Category` has to survive a rule aimed at `CategoryID`.
_NOT_A_DIMENSION = ("id", "key", "guid", "code", "url", "image", "pic", "photo")


class Unqueryable(RuntimeError):
    """The engine answered nothing at all, so silence is not a finding."""


@dataclass(frozen=True)
class Slice:
    label: str
    value: float
    #: Where this group falls in time, as an ISO date, when the model carries
    #: something to say so. Empty when it does not.
    #:
    #: This is what makes "in date order" an option rather than a guess. Power
    #: BI records a column's display order in a sort-by column that this file's
    #: reader does not expose, so `Jan, Feb, Mar` cannot be ordered by reading
    #: the labels -- alphabetically that is April first. But the `Calendar`
    #: table those labels come from also holds real dates, and the earliest
    #: date in each group is a fact in the data rather than an inference about
    #: what the words mean. That is what this holds.
    order: str = ""


@dataclass(frozen=True)
class Breakdown:
    """One measure split by one dimension."""

    measure: str
    #: `Item[Category]`, the way the grain machinery names a column.
    by: str
    table: str
    column: str
    slices: tuple[Slice, ...] = ()
    #: The query that produced these numbers.
    sql: str = ""
    #: How many groups were folded into the last slice, if any.
    folded: int = 0
    #: Why there is nothing to draw. Empty when there is.
    reason: str = ""
    #: The measure's figure for the whole model, when it has one. Carried so
    #: the parts can be checked against the whole rather than assumed to match.
    whole: float | None = None
    #: Whether the parts actually sum to the whole. Measured, never assumed --
    #: see `_adds_up`.
    additive: bool = False

    @property
    def total(self) -> float:
        return sum(s.value for s in self.slices)

    @property
    def drawable(self) -> bool:
        return len(self.slices) >= MIN_CLASSES


@dataclass(frozen=True)
class Dashboard:
    """Everything a chart grid needs for one measure."""

    measure: str
    available: bool
    reason: str = ""
    breakdowns: tuple[Breakdown, ...] = ()
    dimensions: tuple[dict[str, str], ...] = field(default_factory=tuple)
    #: The years this measure can be restricted to. Empty when the model has no
    #: calendar a filter could safely stand on -- see `usable_years`.
    years: tuple[int, ...] = ()
    #: The year actually applied, or None for every year.
    year: int | None = None


def _named_like_an_id(column: str) -> bool:
    lowered = column.casefold().strip()
    if lowered in _NOT_A_DIMENSION:
        return True
    if any(lowered.endswith(suffix) for suffix in _NOT_A_DIMENSION):
        return True
    # Power BI writes these itself when somebody uses "find clusters" on a
    # visual. They are a machine's working notes, not a business dimension.
    return "(clusters)" in lowered


def _unreadable(sample: Any) -> bool:
    """True for a value no axis label can carry.

    Checked by looking rather than by naming, because the names lie in both
    directions here: `DM_Pic_fl` holds URLs and does not end in `pic`, while
    plenty of honest columns would trip a substring rule.
    """
    if isinstance(sample, (bytes, bytearray, memoryview)):
        return True
    if isinstance(sample, str):
        text = sample.strip().casefold()
        if text.startswith(("http://", "https://", "data:", "www.", "/9j/")):
            return True
        # A label this long is not a label; it is a paragraph in an axis.
        if len(text) > 60:
            return True
    return False


#: Column types that carry a point in time.
_DATE_TYPES = ("date", "time")


def _date_columns(model, table: str) -> list[str]:
    """Date-typed columns on one table, in model order."""
    return [
        column.name
        for column in model.columns
        if column.table == table
        and any(kind in (column.data_type or "").casefold() for kind in _DATE_TYPES)
    ]


def calendar_column(model) -> tuple[str, str] | None:
    """The one date column a year filter can safely stand on, if there is one.

    Deliberately conservative, and it declines more often than it accepts.

    The table must be a *leaf* -- pointed at, and pointing at nothing itself.
    Being pointed at is not enough on its own: in Microsoft's Sales & Returns,
    `Customer` points at `Sales`, which makes `Sales` something-pointed-at
    while it is plainly the fact table, and it carries a `Date` column of its
    own. The leaf test keeps `Calendar` and drops `Sales`.

    And if two leaves still qualify there is no principled way to choose
    between them, so the filter is not offered at all rather than applied to
    whichever happened to sort first.

    Whether the column can actually be *reached* from a given measure is a
    separate question this cannot answer, because it depends on the measure.
    `usable_years` settles that by trying it.
    """
    referenced = {r.to_table for r in model.relationships}
    references = {r.from_table for r in model.relationships}
    found = [
        (table, column)
        for table in sorted(referenced - references)
        for column in _date_columns(model, table)
    ]
    return found[0] if len(found) == 1 else None


def available_years(model, connection) -> list[int]:
    """Every year the model's calendar actually contains."""
    calendar = calendar_column(model)
    if calendar is None:
        return []
    table, column = calendar
    try:
        rows = connection.execute(
            f'SELECT DISTINCT EXTRACT(YEAR FROM "{table.replace(chr(34), chr(34) * 2)}"'
            f'."{column.replace(chr(34), chr(34) * 2)}") AS y '
            f'FROM "{table.replace(chr(34), chr(34) * 2)}" WHERE y IS NOT NULL ORDER BY y'
        ).fetchall()
    except Exception:  # noqa: BLE001 - a calendar that will not read is no filter
        return []
    return [int(row[0]) for row in rows if row[0] is not None]


def usable_years(model, connection, measure: str) -> list[int]:
    """The years this measure can actually be filtered to.

    Verified by attempting the translation rather than reasoned about, because
    the thing that decides it -- whether a join path reaches the calendar from
    the table this particular measure reads -- is exactly what the translator
    already works out. Store Sales is why: its `Fiscal calendar` is a proper
    leaf holding real dates, and no active relationship joins it to `Sales`, so
    a year filter there would silently drop every row. Offering the control and
    having it return nothing is worse than not offering it.
    """
    from concordance.generate.sql import Status, translate

    calendar = calendar_column(model)
    if calendar is None:
        return []
    found = next((m for m in model.measures if m.name == measure), None)
    if found is None:
        return []

    years = available_years(model, connection)
    if not years:
        return []

    table, column = calendar
    probe = translate(model, found, only_year=(table, column, years[0]))
    return years if probe.status is Status.EXACT else []


def _anchors(model, connection, table: str, column: str) -> dict[str, str]:
    """Each group's place in time, but only where the groups *are* places in time.

    Two questions, and the second is the one that matters. Getting the earliest
    date per group is easy. Deciding whether that date means anything is not,
    and it cannot be decided by reading the column's name.

    The test is whether the groups partition time: sorted by their earliest
    date, does each group finish before the next one starts? Real periods do.
    `Store[Chain]` does not -- both chains have been opening stores across the
    same decade, so "Lindseys, then Fashions Direct" would be an ordering of
    nothing, silently presented as a chronology.

    It also, correctly, declines `Fiscal calendar[FiscalMonth]` in Store Sales:
    that table covers three years, so its "Jan" is January 2013 *and* January
    2014, and there is no single point in time to put it at. Restrict the model
    to one year and the same column passes, because then there is.
    """
    dates = _date_columns(model, table)
    if not dates:
        return {}
    quoted_table = table.replace('"', '""')
    quoted_label = column.replace('"', '""')
    quoted_date = dates[0].replace('"', '""')
    try:
        rows = connection.execute(
            f'SELECT "{quoted_label}", MIN("{quoted_date}"), MAX("{quoted_date}") '
            f'FROM "{quoted_table}" GROUP BY 1 ORDER BY 2'
        ).fetchall()
    except Exception:  # noqa: BLE001 - no anchor is a missing option, not a failure
        return {}

    spans = [(str(label), first, last) for label, first, last in rows if first is not None]
    if len(spans) < MIN_CLASSES:
        return {}
    # Sorted by start already, so one pass settles it: any group still running
    # when the next one begins means these are not periods.
    for (_, _, ends), (_, starts, _) in zip(spans, spans[1:]):
        if ends >= starts:
            return {}
    return {label: str(first) for label, first, _ in spans}


def _dimension_tables(model) -> set[str]:
    """Tables something points at.

    Deliberately *not* the leaf-only rule the grain picker uses. A snowflake's
    middle table -- `Store`, pointed at by `Sales` and pointing at `District` --
    is excluded by that rule and holds the two best dimensions in the file.
    What matters for a chart is only that the table is on the "one" side of
    some relationship, so grouping by it does not multiply rows.
    """
    return {r.to_table for r in model.relationships}


def chartable(model, connection) -> list[tuple[str, str, int]]:
    """Every (table, column, distinct-count) worth charting, measured.

    One query per candidate, each a `COUNT(DISTINCT ...)` over a dimension
    table -- hundreds of rows, not the fact table's million -- so the whole
    sweep costs less than the one query the card above it already ran.
    """
    dimensions = _dimension_tables(model)
    keys = {(r.to_table, r.to_column) for r in model.relationships}

    found: list[tuple[str, str, int]] = []
    tried = 0
    failed = 0
    for column in model.columns:
        if column.table not in dimensions:
            continue
        if (column.table, column.name) in keys:
            continue  # a join key groups by an opaque id
        if _named_like_an_id(column.name):
            continue

        tried += 1
        table = column.table.replace('"', '""')
        name = column.name.replace('"', '""')
        try:
            row = connection.execute(
                f'SELECT COUNT(DISTINCT "{name}"), MIN("{name}") FROM "{table}"'
            ).fetchone()
        except Exception:  # noqa: BLE001 - a column that will not count is skipped
            # Calculated columns that were never materialised land here, which
            # is the right outcome: they are not in the data to group by.
            failed += 1
            continue
        if not row:
            continue
        count, sample = row[0] or 0, row[1]
        if not (MIN_CLASSES <= count <= MAX_CLASSES):
            continue
        if _unreadable(sample):
            continue
        found.append((column.table, column.name, count))

    # Fewest groups first: a two-way split is the clearest chart on the page
    # and belongs at the front of the row.
    found.sort(key=lambda entry: (entry[2], entry[0], entry[1]))

    # Skipping a column that will not count is right; skipping *every* column
    # is not a finding about the model, it is the engine being unusable, and
    # reporting it as "nothing here is chartable" would be a lie told
    # confidently. This project shipped that lie once -- a threaded server
    # shared one DuckDB connection between requests, every query failed, and
    # the page said the model had no dimensions.
    if tried and failed == tried:
        raise Unqueryable(
            f"none of the {tried} candidate columns could be counted, so this "
            "is the query engine failing rather than the model having nothing "
            "to chart"
        )
    return found


def _spread(candidates: list[tuple[str, str, int]], most: int) -> list[tuple[str, str, int]]:
    """At most one column per table.

    Four charts of `District`, `DistrictName` and `DM` are three drawings of
    the same nine districts. Taking one column per table is what makes the row
    show four different angles -- which is also how the dashboards this is
    modelled on are laid out.
    """
    seen: set[str] = set()
    out: list[tuple[str, str, int]] = []
    for table, column, count in candidates:
        if table in seen:
            continue
        seen.add(table)
        out.append((table, column, count))
        if len(out) >= most:
            break
    return out


#: How close the parts have to come to the whole to count as adding up.
#: Relative, because a float sum over a million rows does not reproduce a
#: single-pass sum bit for bit and never has.
_TOLERANCE = 1e-9


def _adds_up(parts: float, whole: float | None) -> bool:
    """Whether the parts really do sum to the whole -- measured, not assumed.

    This matters more than it looks. `Sales` splits additively: the chains sum
    to the company. `Average Selling Area Size` does not, and nothing in its
    name or its DAX says so at a glance -- you have to notice that the outer
    aggregate is an `AVG`, and that the average of two chains' averages is not
    the average of the stores. Printing "totals 59,302" under that chart would
    be stating a figure that is not a quantity of anything.

    So it is checked by arithmetic: run the measure ungrouped, run it grouped,
    and see whether the two agree. A ratio, an average, a distinct count and a
    period-over-period delta all fail that check, and all of them fail it for
    the same honest reason.
    """
    if whole is None:
        return False
    scale = max(abs(parts), abs(whole))
    if scale == 0:
        return True
    return abs(parts - whole) / scale <= _TOLERANCE


def one(
    model,
    connection,
    measure: str,
    table: str,
    column: str,
    whole: float | None = None,
    year: int | None = None,
) -> Breakdown:
    """Run one measure grouped by one column, optionally within one year."""
    from concordance.generate.sql import Status, translate

    by = f"{table}[{column}]"
    only_year = None
    if year is not None:
        calendar = calendar_column(model)
        if calendar is not None:
            only_year = (calendar[0], calendar[1], year)

    found = next((m for m in model.measures if m.name == measure), None)
    translated = (
        translate(model, found, grain=(by,), only_year=only_year)
        if found is not None
        else None
    )
    if translated is None:
        return Breakdown(
            measure=measure, by=by, table=table, column=column,
            reason=f"{measure} is not a measure in this model",
        )
    if translated.status is not Status.EXACT:
        return Breakdown(
            measure=measure, by=by, table=table, column=column,
            reason=translated.reason,
        )

    try:
        cursor = connection.execute(translated.sql)
        columns = [c[0] for c in cursor.description or []]
        rows = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001 - a bad query is a finding, not a crash
        return Breakdown(
            measure=measure, by=by, table=table, column=column,
            sql=translated.sql,
            reason=f"the generated query did not run: {exc}",
        )

    # The measure by name, for the same reason the KPI card does it: a grouped
    # query selects the dimension first, and reading position zero charts the
    # labels against themselves.
    wanted = measure.casefold()
    at = next((i for i, name in enumerate(columns) if name.casefold() == wanted), None)
    if at is None:
        return Breakdown(
            measure=measure, by=by, table=table, column=column,
            sql=translated.sql,
            reason=f"the query returned no column named {measure!r}",
        )
    label_at = 0 if at != 0 else (1 if len(columns) > 1 else 0)

    # Where each group sits in time, when the model can say. Fetched once per
    # breakdown, against the dimension table only.
    anchors = _anchors(model, connection, table, column)

    slices = [
        Slice(
            label=str(row[label_at]),
            value=float(row[at]),
            order=anchors.get(str(row[label_at]), ""),
        )
        for row in rows
        # A group with no figure is dropped rather than drawn as zero: an empty
        # bar and a bar at zero look identical and mean different things.
        if row[at] is not None
        and isinstance(row[at], (int, float))
        and not isinstance(row[at], bool)
    ]
    slices.sort(key=lambda s: s.value, reverse=True)

    folded = 0
    if len(slices) > MAX_SLICES:
        tail = slices[MAX_SLICES:]
        folded = len(tail)
        # The folded slice carries no anchor and cannot: it is several groups
        # from several points in time. In date order it sorts last, which is
        # the honest place for a group that is not one place in time.
        slices = slices[:MAX_SLICES] + [
            Slice(label=f"{folded} more", value=sum(s.value for s in tail))
        ]

    parts = sum(s.value for s in slices)
    return Breakdown(
        measure=measure, by=by, table=table, column=column,
        slices=tuple(slices), sql=translated.sql, folded=folded,
        whole=whole, additive=_adds_up(parts, whole),
    )


def _whole(model, connection, measure: str, year: int | None) -> float | None:
    """The measure's single figure over exactly the rows the charts cover.

    The year has to be carried into this too. Comparing one year's parts
    against every year's total finds them unequal and concludes the measure is
    non-additive -- so a plain `SUM` filtered to 2019 would announce itself as
    "an average or a ratio", which is both wrong and confidently worded. The
    additivity test is only a test of additivity when both sides read the same
    rows.
    """
    from concordance.generate.evaluate import evaluate
    from concordance.generate.sql import Status, translate

    if year is None:
        found = evaluate(model, connection=connection).by_name().get(measure)
        return found.value if found is not None else None

    calendar = calendar_column(model)
    found = next((m for m in model.measures if m.name == measure), None)
    if calendar is None or found is None:
        return None
    rendered = translate(
        model, found, only_year=(calendar[0], calendar[1], year)
    )
    if rendered.status is not Status.EXACT:
        return None
    try:
        rows = connection.execute(rendered.sql).fetchmany(2)
    except Exception:  # noqa: BLE001 - no whole is a missing check, not a crash
        return None
    # Exactly one row, or there is no single figure to check the parts against.
    if len(rows) != 1 or not rows[0]:
        return None
    value = rows[0][-1]
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def build(
    model, connection, measure: str, most: int = 4, year: int | None = None
) -> Dashboard:
    """Every chart worth drawing for one measure, optionally within one year."""
    if connection is None:
        return Dashboard(measure=measure, available=False, reason="no data is loaded")

    # The whole-model figure first, so each split can be checked against it
    # rather than asserted to match. One extra query, run once.
    offered = usable_years(model, connection, measure)
    # A year nobody offered is not applied. Silently charting every year while
    # the control says otherwise would be the worst of the available outcomes.
    if year is not None and year not in offered:
        year = None

    # The whole-model figure is only a fair check on the parts when both cover
    # the same rows, so a filtered chart is compared against a filtered whole.
    whole = _whole(model, connection, measure, year)

    try:
        candidates = chartable(model, connection)
    except Unqueryable as exc:
        return Dashboard(measure=measure, available=False, reason=str(exc))

    drawn: list[Breakdown] = []
    for table, column, _count in _spread(candidates, most):
        result = one(model, connection, measure, table, column, whole=whole, year=year)
        if result.drawable:
            drawn.append(result)

    drawn.sort(key=lambda b: len(b.slices))

    return Dashboard(
        measure=measure,
        available=bool(drawn),
        reason=(
            ""
            if drawn
            else (
                "Nothing in this model splits this measure into between "
                f"{MIN_CLASSES} and {MAX_CLASSES} readable groups, so there is "
                "no chart to draw that a reader would not find harder than the "
                "figure itself."
            )
        ),
        breakdowns=tuple(drawn),
        dimensions=tuple(
            {"table": t, "column": c, "value": f"{t}[{c}]"} for t, c, _ in candidates
        ),
        years=tuple(offered),
        year=year,
    )

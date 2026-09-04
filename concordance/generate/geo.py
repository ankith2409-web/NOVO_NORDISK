"""One measure, plotted where it happened.

A map is the one chart on a dashboard that cannot be faked. Every other shape
can be drawn from the numbers alone; a map needs to know *where*, and a
semantic model either records that or it does not.

So this module is mostly a set of refusals. Store Sales carries `Postal code`,
`Territory` and `City Name` and no coordinates at all -- turning those into
positions would mean geocoding, which is a lookup against data that is not in
the file, and a map drawn from guessed coordinates is a map that says a store
is somewhere it is not. That model gets no map, and the reason is stated.

Microsoft's Sales & Returns is the other case: its `Store` table holds real
`Latitude` and `Longitude` for all fourteen stores, clustered around Chicago,
which is exactly what its own report header says. There the map is drawn from
the file's own numbers, and the same measure that fills the card above it is
what sizes each point.

The columns are found by name because that is how they are declared -- a
column called `Latitude` holding a number between -90 and 90 is a latitude,
and there is no structural signal that says so instead. But the *values* are
checked: a "latitude" outside its legal range is not one, whatever it is
called, and a pair that fails that test is refused rather than plotted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Legal bounds. A column named `Latitude` holding 300 is not a latitude, and
#: plotting it would put a point off the map with no indication why.
LAT_RANGE = (-90.0, 90.0)
LON_RANGE = (-180.0, 180.0)

#: What each half of a coordinate pair tends to be called.
_LAT_NAMES = ("latitude", "lat")
_LON_NAMES = ("longitude", "longitude_", "long", "lon", "lng")

#: Points drawn. Above this a map of a warehouse's worth of locations becomes a
#: solid block of ink that answers nothing.
MAX_POINTS = 400


@dataclass(frozen=True)
class Place:
    """One location, and the measure's value there."""

    label: str
    latitude: float
    longitude: float
    value: float


@dataclass(frozen=True)
class Atlas:
    """Everything a map needs for one measure, or why there is none."""

    measure: str
    available: bool
    reason: str = ""
    #: `Store`, the table the coordinates were read from.
    table: str = ""
    #: `Store[Store]`, the column naming each point.
    label_column: str = ""
    places: tuple[Place, ...] = field(default_factory=tuple)
    #: The query that produced the values, so a point can be checked.
    sql: str = ""

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """`(min_lat, min_lon, max_lat, max_lon)` over the plotted points."""
        if not self.places:
            return (0.0, 0.0, 0.0, 0.0)
        lats = [p.latitude for p in self.places]
        lons = [p.longitude for p in self.places]
        return (min(lats), min(lons), max(lats), max(lons))


def usable(latitude: Any, longitude: Any) -> tuple[float, float] | None:
    """One coordinate pair, if it is a legal one.

    Checked rather than trusted, and it is the check the whole module rests on:
    a column called `Latitude` holding 300 is not a latitude, whatever the
    model says, and a point silently drawn off the edge of the box is worse
    than a point not drawn -- it moves every other point to make room for it
    and there is nothing on screen to say so.
    """
    if latitude is None or longitude is None:
        return None
    # Booleans are ints in Python and would pass a numeric test as 0 and 1,
    # which is a real place off the coast of Africa.
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    if lat != lat or lon != lon:  # NaN, which compares false against everything
        return None
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1]):
        return None
    if not (LON_RANGE[0] <= lon <= LON_RANGE[1]):
        return None
    return lat, lon


def _matches(column: str, names: tuple[str, ...]) -> bool:
    cleaned = column.casefold().replace(" ", "").replace("_", "")
    return cleaned in names


def coordinate_columns(model, table: str) -> tuple[str, str] | None:
    """The latitude and longitude columns on one table, if it has a pair.

    Both halves or neither. A table with a latitude and no longitude cannot be
    plotted, and picking some other numeric column to stand in for the missing
    half would be inventing a position.
    """
    lat = next(
        (c.name for c in model.columns if c.table == table and _matches(c.name, _LAT_NAMES)),
        None,
    )
    lon = next(
        (c.name for c in model.columns if c.table == table and _matches(c.name, _LON_NAMES)),
        None,
    )
    return (lat, lon) if lat and lon else None


def _label_column(model, table: str, coordinates: tuple[str, str]) -> str:
    """What to call each point.

    The table's own name-like column, preferred over an id: a map whose points
    are labelled `14`, `15`, `16` is a map nobody can read. Falls back to the
    first text column, and finally to the table name itself.
    """
    from concordance.generate.breakdown import _named_like_an_id

    columns = [c for c in model.columns if c.table == table and c.name not in coordinates]
    exact = next((c.name for c in columns if c.name.casefold() in (table.casefold(), "name")), "")
    if exact:
        return exact
    readable = next(
        (
            c.name
            for c in columns
            if not _named_like_an_id(c.name)
            and "char" in (c.data_type or "").casefold() + "string"
        ),
        "",
    )
    return readable or (columns[0].name if columns else "")


def mappable_tables(model) -> list[str]:
    """Every table carrying a usable coordinate pair, in model order."""
    seen: list[str] = []
    for column in model.columns:
        if column.table in seen:
            continue
        if coordinate_columns(model, column.table):
            seen.append(column.table)
    return seen


def build(model, connection, measure: str) -> Atlas:
    """One measure, per location, ready to plot."""
    from concordance.generate.sql import Status, translate

    if connection is None:
        return Atlas(measure=measure, available=False, reason="no data is loaded")

    tables = mappable_tables(model)
    if not tables:
        return Atlas(
            measure=measure,
            available=False,
            reason=(
                "No table in this model carries latitude and longitude, so there is "
                "nothing to place on a map. Names like a postal code or a city could "
                "be turned into positions only by looking them up somewhere outside "
                "this file, and a map drawn from a guess would put a location "
                "somewhere it is not."
            ),
        )

    table = tables[0]
    coordinates = coordinate_columns(model, table)
    assert coordinates is not None  # mappable_tables only returns tables with a pair
    latitude, longitude = coordinates
    label = _label_column(model, table, coordinates)
    if not label:
        return Atlas(
            measure=measure,
            available=False,
            reason=f"{table} has coordinates but no column to name each point by",
        )

    found = next((m for m in model.measures if m.name == measure), None)
    if found is None:
        return Atlas(
            measure=measure, available=False, reason=f"{measure} is not a measure in this model"
        )

    # Grouped by the label, so one row is one point. The coordinates come from a
    # second, tiny query against the dimension table rather than being forced
    # into the measure's own query, which would have to carry them through the
    # GROUP BY and change what the translation says.
    rendered = translate(model, found, grain=(f"{table}[{label}]",))
    if rendered.status is not Status.EXACT:
        return Atlas(measure=measure, available=False, reason=rendered.reason, table=table)

    quoted = table.replace('"', '""')
    try:
        rows = connection.execute(rendered.sql).fetchall()
        columns = [c[0] for c in connection.execute(rendered.sql).description or []]
        points = connection.execute(
            f'SELECT "{label.replace(chr(34), chr(34) * 2)}", '
            f'"{latitude.replace(chr(34), chr(34) * 2)}", '
            f'"{longitude.replace(chr(34), chr(34) * 2)}" FROM "{quoted}"'
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - a bad query is a finding, not a crash
        return Atlas(
            measure=measure,
            available=False,
            reason=f"the generated query did not run: {exc}",
            table=table,
            sql=rendered.sql,
        )

    # The measure by name, for the reason the KPI card does it: a grouped query
    # selects the grain first, and reading position zero plots the labels.
    wanted = measure.casefold()
    at = next((i for i, name in enumerate(columns) if name.casefold() == wanted), None)
    if at is None:
        return Atlas(
            measure=measure,
            available=False,
            reason=f"the query returned no column named {measure!r}",
            table=table,
            sql=rendered.sql,
        )
    label_at = 0 if at != 0 else 1

    where: dict[str, tuple[float, float]] = {}
    for name, lat, lon in points:
        pair = usable(lat, lon)
        if pair is not None:
            where[str(name)] = pair

    places: list[Place] = []
    for row in rows:
        name = str(row[label_at])
        value = row[at]
        if name not in where or value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        lat, lon = where[name]
        places.append(Place(label=name, latitude=lat, longitude=lon, value=float(value)))

    if not places:
        return Atlas(
            measure=measure,
            available=False,
            reason=(
                f"{table} carries coordinates, but none of them line up with a value "
                f"for {measure}, so there is nothing to place."
            ),
            table=table,
            sql=rendered.sql,
        )

    places.sort(key=lambda p: abs(p.value), reverse=True)
    return Atlas(
        measure=measure,
        available=True,
        table=table,
        label_column=label,
        places=tuple(places[:MAX_POINTS]),
        sql=rendered.sql,
    )

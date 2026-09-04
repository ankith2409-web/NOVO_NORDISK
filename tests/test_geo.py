"""Plotting a measure where it happened -- and refusing to when it cannot be.

A map is the one chart that can be wrong in a way no amount of checking the
numbers catches: every figure can be right while every point is in the wrong
place. So the tests here are mostly about the refusals.

Two fixtures, and they are opposites by luck rather than by design. Microsoft's
Sales & Returns carries real `Latitude` and `Longitude` on its `Store` table.
Store Sales carries `Postal code`, `Territory` and `City Name` and no
coordinates at all -- exactly the case where a tool that wanted a map badly
enough would reach for geocoding and start drawing stores in the wrong city.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.generate import geo
from concordance.generate.evaluate import open_data

SALES = Path("data/models/Sales_Returns_Sample.pbix")
STORE = Path("data/models/StoreSales.pbix")


def _opened(path: Path):
    if not path.exists():
        pytest.skip(f"model not present: {path}")
    model = PbixAdapter().extract(str(path))
    connection, _rows, reason = open_data(model)
    if connection is None:
        pytest.skip(reason)
    return model, connection


@pytest.fixture(scope="module")
def sales():
    model, connection = _opened(SALES)
    try:
        yield model, connection
    finally:
        connection.close()


@pytest.fixture(scope="module")
def store():
    model, connection = _opened(STORE)
    try:
        yield model, connection
    finally:
        connection.close()


# -- finding the coordinates ---------------------------------------------------


def test_the_store_table_offers_a_coordinate_pair(sales) -> None:
    model, _ = sales
    assert geo.coordinate_columns(model, "Store") == ("Latitude", "Longitude")


def test_a_table_without_coordinates_offers_nothing(sales) -> None:
    model, _ = sales
    assert geo.coordinate_columns(model, "Sales") is None


def test_a_model_with_no_coordinates_anywhere_has_no_mappable_table(store) -> None:
    """Store Sales has postal codes and city names and no latitude at all."""
    model, _ = store
    assert geo.mappable_tables(model) == []


def test_half_a_pair_is_not_a_pair() -> None:
    """A latitude with no longitude cannot be plotted, and inventing the other
    half from some nearby numeric column is how a map ends up lying."""

    class Column:
        def __init__(self, table, name):
            self.table, self.name, self.data_type = table, name, "double"

    class Model:
        columns = [Column("Site", "Latitude"), Column("Site", "Headcount")]

    assert geo.coordinate_columns(Model(), "Site") is None


# -- the map itself ------------------------------------------------------------


def test_net_sales_plots_at_the_files_own_coordinates(sales) -> None:
    model, connection = sales
    atlas = geo.build(model, connection, "Net Sales")
    assert atlas.available, atlas.reason
    assert atlas.table == "Store"
    assert len(atlas.places) >= 2
    # Chicago, which is what the report's own header says.
    low_lat, low_lon, high_lat, high_lon = atlas.bounds
    assert 41 < low_lat <= high_lat < 43
    assert -89 < low_lon <= high_lon < -87


def test_every_plotted_coordinate_is_a_legal_one(sales) -> None:
    model, connection = sales
    for place in geo.build(model, connection, "Net Sales").places:
        assert geo.LAT_RANGE[0] <= place.latitude <= geo.LAT_RANGE[1]
        assert geo.LON_RANGE[0] <= place.longitude <= geo.LON_RANGE[1]


def test_points_are_labelled_by_name_not_by_id(sales) -> None:
    """A map whose points read `14`, `15`, `16` is a map nobody can use."""
    model, connection = sales
    atlas = geo.build(model, connection, "Net Sales")
    assert not all(p.label.strip().isdigit() for p in atlas.places)


def test_the_query_is_the_measures_own_sql(sales) -> None:
    model, connection = sales
    atlas = geo.build(model, connection, "Net Sales")
    assert "GROUP BY" in atlas.sql
    assert "Store" in atlas.sql


def test_a_model_without_coordinates_says_why_rather_than_drawing_nothing(store) -> None:
    model, connection = store
    atlas = geo.build(model, connection, "Sales")
    assert not atlas.available
    assert "latitude" in atlas.reason.casefold()
    assert atlas.places == ()


def test_a_measure_that_is_not_in_the_model_is_named_in_the_refusal(sales) -> None:
    model, connection = sales
    atlas = geo.build(model, connection, "Not A Measure")
    assert not atlas.available
    assert "Not A Measure" in atlas.reason


def test_no_data_is_not_a_crash(sales) -> None:
    model, _ = sales
    atlas = geo.build(model, None, "Net Sales")
    assert not atlas.available and atlas.reason


def test_bounds_of_an_empty_atlas_are_not_an_exception() -> None:
    assert geo.Atlas(measure="x", available=False).bounds == (0.0, 0.0, 0.0, 0.0)


# -- the coordinate check ------------------------------------------------------
#
# This is the guard the whole module rests on, and no fixture exercises it:
# both sample files hold coordinates that are already legal. Left untested it
# survives being deleted, which was checked by deleting it.


@pytest.mark.parametrize(
    "latitude, longitude",
    [
        (41.9, -87.6),  # Chicago, the case that must pass
        (0, 0),
        (-90, 180),
        (90, -180),
        ("41.9", "-87.6"),  # a warehouse handing back decimals as text
    ],
)
def test_a_legal_pair_is_accepted(latitude, longitude) -> None:
    assert geo.usable(latitude, longitude) is not None


@pytest.mark.parametrize(
    "latitude, longitude",
    [
        (300, -87.6),  # a "latitude" that is not one
        (41.9, 400),
        (-91, 0),
        (0, -181),
        (None, -87.6),
        (41.9, None),
        ("north", "west"),
        (True, False),  # booleans are ints, and 0,0 is a real place
        (float("nan"), 0),
    ],
)
def test_an_illegal_pair_is_refused_rather_than_plotted(latitude, longitude) -> None:
    assert geo.usable(latitude, longitude) is None

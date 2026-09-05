"""Which basemap a server is configured for, and how it says so.

Google Maps needs an API key, so the choice belongs to whoever runs the server
rather than to the reader. What these pin is the handling of that key, because
this project has one hard rule about credentials and this is the first one it
deliberately sends to the browser.

That is not an exception to the rule; it is the rule applied correctly. A Maps
*browser* key is designed to travel to the browser -- it is visible in the page
source of every site that uses one, and Google's documented protection is an
HTTP-referrer restriction on the key rather than concealment. A model
provider's key is a bearer secret and never leaves the server. The tests below
say which is which, so a later change cannot quietly turn one into the other.

The key is read from the environment and has no default, so a repository can
never carry one.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from concordance.adapters.pbix import PbixAdapter
from concordance.graph.csg import SemanticGraph
from concordance.web.api import ApiContext, _MAPS_KEY_VAR, atlas, values

SALES = Path("data/models/Sales_Returns_Sample.pbix")


@pytest.fixture(scope="module")
def context():
    if not SALES.exists():
        pytest.skip(f"model not present: {SALES}")
    return ApiContext(graph=SemanticGraph(PbixAdapter().extract(str(SALES))))


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    """Each test states its own configuration, so none inherits the machine's."""
    monkeypatch.delenv(_MAPS_KEY_VAR, raising=False)


def test_a_server_with_no_key_reports_the_built_in_map(context) -> None:
    found = atlas(context, {})
    assert found["basemap"] == "tiles"
    assert found["maps_key"] == ""


def test_a_configured_key_selects_google(context, monkeypatch) -> None:
    monkeypatch.setenv(_MAPS_KEY_VAR, "AIza-EXAMPLE")
    found = atlas(context, {})
    assert found["basemap"] == "google"
    assert found["maps_key"] == "AIza-EXAMPLE"


def test_surrounding_whitespace_does_not_count_as_a_key(context, monkeypatch) -> None:
    """A variable set to a blank line is a variable nobody meant to set, and
    `https://maps.googleapis.com/...?key=` fails in a way that is hard to read
    back to its cause."""
    monkeypatch.setenv(_MAPS_KEY_VAR, "   ")
    found = atlas(context, {})
    assert found["basemap"] == "tiles"
    assert found["maps_key"] == ""


def test_a_key_is_trimmed_before_it_reaches_a_url(context, monkeypatch) -> None:
    monkeypatch.setenv(_MAPS_KEY_VAR, "  AIza-EXAMPLE\n")
    assert atlas(context, {})["maps_key"] == "AIza-EXAMPLE"


def test_the_points_are_the_same_whichever_map_is_under_them(
    context, monkeypatch
) -> None:
    """The basemap is scenery. Every figure comes from the query."""
    plain = atlas(context, {})
    monkeypatch.setenv(_MAPS_KEY_VAR, "AIza-EXAMPLE")
    google = atlas(context, {})
    assert plain["places"] == google["places"]
    assert plain["sql"] == google["sql"]
    assert plain["available"] == google["available"]


def test_there_is_no_default_key_anywhere(context) -> None:
    """A key committed to a repository is a key on every fork of it."""
    assert os.environ.get(_MAPS_KEY_VAR) is None
    assert atlas(context, {})["maps_key"] == ""


def test_no_other_endpoint_leaks_the_key(context, monkeypatch) -> None:
    """Only the map needs it. A key sprayed across every payload is a key that
    ends up in a log, a snapshot, or an audit pack."""
    monkeypatch.setenv(_MAPS_KEY_VAR, "AIza-EXAMPLE")
    assert "AIza-EXAMPLE" not in repr(values(context, {}))


def test_a_model_provider_key_is_never_sent_to_the_browser(
    context, monkeypatch
) -> None:
    """The distinction this whole file exists to hold: a Maps browser key is
    designed to be public and referrer-restricted; a provider key is a bearer
    secret. One may reach the page and the other must not."""
    monkeypatch.setenv("GEMINI_API_KEY", "secret-bearer-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-bearer-token")
    monkeypatch.setenv(_MAPS_KEY_VAR, "AIza-EXAMPLE")
    body = repr(atlas(context, {}))
    assert "AIza-EXAMPLE" in body
    assert "secret-bearer-token" not in body

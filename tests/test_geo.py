"""Where a role is, and an Eircode's area, for the part-time page's Near an Eircode."""

from __future__ import annotations

import pytest

from jobfinder.normalize import geo


@pytest.mark.parametrize(("text", "key"), [
    ("D02 X285", "D02"), ("d02x285", "D02"), ("D02", "D02"), (" t12 ", "T12"),
    ("D6W", "D6W"), ("D6W A1C2", "D6W"),
    # Not an Eircode: an unused routing key, letters an Eircode never has, too short.
    ("Z99", None), ("B12", None), ("D0", None), ("D02 AB12", None), ("", None),
])
def test_an_eircode_is_read_for_its_routing_area(text: str, key: str | None) -> None:
    assert geo.routing_area(text) == key


@pytest.mark.parametrize(("location", "county", "near"), [
    ("Navan, Co. Meath, Ireland", "Meath", "C15"),
    ("Dublin 6W", "Dublin", "D6W"),
    ("Headford Road, Galway. H91PX08, Ireland", "Galway", "H91"),
    # A shop or a college round its town.
    ("Homesavers Charleville, Cork", "Cork", "P56"),
    ("Hickey's Pharmacy Navan Shopping Centre, Ireland", "Meath", "C15"),
    # The street goes before the town, and Street is also a village in Westmeath.
    ("Main Street, Mullingar", "Westmeath", "N91"),
    # Not a populated place in GeoNames: found as a post town.
    ("Clonmel, County Tipperary, Ireland", "Tipperary", "E91"),
    ("Dún Laoghaire, Dublin, Ireland", "Dublin", "A96"),
])
def test_a_named_town_places_a_role_exactly(location: str, county: str, near: str) -> None:
    lat, lng, exact = geo.place_point(location, county)
    assert exact
    assert geo.distance_km((lat, lng), geo.area_point(near)[:2]) < 12


def test_a_role_naming_only_its_county_is_placed_at_its_main_town_and_says_so() -> None:
    lat, lng, exact = geo.place_point("Cork, Ireland", "Cork")
    assert not exact
    assert geo.distance_km((lat, lng), geo.area_point("T12")[:2]) < 5
    # "Cork City" is the city, said as a town.
    assert geo.place_point("Cork City", "Cork")[2]


def test_a_place_inside_a_part_needs_a_county_to_check_against() -> None:
    # America is a village in Roscommon.
    assert geo.place_point("United States - Remote, United States of America", None) is None
    assert geo.place_point("Ireland", None) is None
    # A whole part that is one place in all of Ireland still counts.
    assert geo.place_point("Letterkenny", None)[2]


def test_distance_is_as_the_crow_flies() -> None:
    dublin, cork = geo.area_point("D02")[:2], geo.area_point("T12")[:2]
    assert 215 < geo.distance_km(dublin, cork) < 225

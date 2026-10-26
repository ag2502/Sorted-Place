"""Where in Ireland a role is, for "within so many km of my Eircode".

Two tables from GeoNames (CC BY 4.0, credited where they are used), built into
data/ie_places.json by scripts/build_ie_places.py: the 139 Eircode routing areas, each
at its post town, and the towns and suburbs of every county. A full Eircode is licensed
data, so only its first three characters count. That places someone to their routing
area: a few streets in Dublin, a market town and the country round it elsewhere.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from functools import cache

from jobfinder.core.config import DATA_DIR

PLACES = DATA_DIR / "ie_places.json"

# A routing key (letter, two digits; D6W the one exception) and, optionally, the four
# characters that name the address. The letters skip B, G, I, J, L, M, O, Q, S, U and Z.
EIRCODE = re.compile(r"\b(D6W|[AC-FHKNPRTV-Y]\d{2})\s?([0-9AC-FHKNPRTV-Y]{4})?\b", re.IGNORECASE)
_DUBLIN_DISTRICT = re.compile(r"\bdublin\s?(\d{1,2})(w?)\b", re.IGNORECASE)
_PARTS = re.compile(r"\s*(?:[,/|;()]|\s[-–]\s)\s*")
_COUNTY_WORD = re.compile(r"^(?:co\.?|county)\s+", re.IGNORECASE)
# "Cork City", "Kildare Town": the town that shares its county's name, said as a town.
_TOWN_WORD = re.compile(r"\s+(?:city|town)(?:\s+cent(?:re|er))?$", re.IGNORECASE)
# Words of an address that are also the name of a village somewhere ("Street" is one in
# Westmeath), so they only count as a place when they are the whole of a part.
_NOT_TOWNS = {
    "street", "road", "park", "centre", "center", "lane", "quay", "avenue", "square",
    "hill", "place", "way", "court", "house", "close", "grove", "bridge", "cross",
    "village", "college", "school", "north", "south", "east", "west", "city", "town",
    "community", "medical", "outlet", "retail", "shopping", "the", "of", "and",
}


def fold(name: str) -> str:
    """A place name as it is looked up: "Dún Laoghaire" and "dun laoghaire" alike."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(plain.casefold().replace("-", " ").replace("'", "").split())


@cache
def _table() -> dict:
    return json.loads(PLACES.read_text(encoding="utf-8"))


def routing_area(text: str) -> str | None:
    """The routing key of an Eircode, when it is one ("D02 X285" or just "d02")."""
    found = EIRCODE.fullmatch(" ".join((text or "").split()))
    key = found.group(1).upper() if found else None
    return key if key in _table()["areas"] else None


def areas() -> dict[str, list]:
    """Every routing area as [latitude, longitude, post town], by its key."""
    return _table()["areas"]


def area_point(key: str) -> tuple[float, float, str] | None:
    """Where a routing area is measured from, and its post town's name."""
    found = _table()["areas"].get(key)
    return (found[0], found[1], found[2]) if found else None


def place_point(location: str | None, county: str | None) -> tuple[float, float, bool] | None:
    """(latitude, longitude, exact) for a role's location, or None where it names no
    county either. Exact is False for a role placed only to its county, which is then
    measured from the county's biggest place."""
    table = _table()
    text = location or ""
    for code in EIRCODE.finditer(text):
        # A bare "A12" in a street address is not an Eircode; the full seven are.
        if code.group(2) and code.group(1).upper() in table["areas"]:
            lat, lng, _ = table["areas"][code.group(1).upper()]
            return lat, lng, True
    district = _DUBLIN_DISTRICT.search(text)
    if district:
        key = "D6W" if district.group(1) == "6" and district.group(2) else f"D{int(district.group(1)):02d}"
        if key in table["areas"]:
            lat, lng, _ = table["areas"][key]
            return lat, lng, True

    counties = table["counties"]
    parts = []
    for part in _PARTS.split(text):
        part = _COUNTY_WORD.sub("", part.strip())
        parts.append((bool(_TOWN_WORD.search(part)), fold(_TOWN_WORD.sub("", part)).split()))
    # A part that is a place outright, then a place inside one: a shop or a college
    # often goes round its town ("Homesavers Charleville", "Navan Shopping Centre").
    # Inside a part needs the county to check against: "United States of America" has
    # America in it, and America is a village in Roscommon.
    for whole in (True, False) if county else (True,):
        for as_town, words in parts:
            sizes = [len(words)] if whole else range(min(3, len(words)), 0, -1)
            for size in sizes:
                for start in range(len(words) - size + 1):
                    name = " ".join(words[start:start + size])
                    if (name.title() in counties and not as_town) or (
                            not whole and name in _NOT_TOWNS):
                        continue
                    found = _lookup(name, county)
                    if found:
                        return found[0], found[1], True
    if county in counties:
        lat, lng = counties[county]
        return lat, lng, False
    return None


def _lookup(name: str, county: str | None) -> list[float] | None:
    places = _table()["places"]
    if county in places:
        return places[county].get(name)
    # With no county to go on, only a name that is one place in all of Ireland.
    hits = [named[name] for named in places.values() if name in named]
    return hits[0] if len(hits) == 1 else None


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """As the crow flies, which is what "within 10 km" means to most people."""
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(h))

"""Build data/ie_places.json, where the part-time page's "Near my Eircode" measures from.

Reads two GeoNames files (CC BY 4.0, credited on the page) and Wikipedia's list of
routing areas, downloaded by hand:

    https://download.geonames.org/export/zip/IE.zip     -> the Eircode routing areas
    https://download.geonames.org/export/dump/IE.zip    -> every populated place
    (the county codes are fixed below, from admin2Codes.txt)
    https://en.wikipedia.org/w/index.php?title=List_of_Eircode_routing_areas_in_Ireland&action=raw

    python scripts/build_ie_places.py ZIP_IE.txt DUMP_IE.txt WIKIPEDIA.txt

GeoNames puts some routing areas at the wrong town (C15 at Trim; its post town is
Navan, and A82 is Virginia, not Kells). Each area is put at its post town from the
Wikipedia list, found among GeoNames' towns, and at GeoNames' own point only where
the post town is not one place (the Dublin districts, Cork city's north and south).

Run again only if GeoNames corrects a place; the output is committed.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jobfinder.normalize.geo import distance_km, fold  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "ie_places.json"

# GeoNames' second-level divisions for Ireland, as the counties the site names. Dublin
# and Cork, Galway, Limerick and Waterford come as city and county councils.
COUNTY_CODES = {
    "01": "Carlow", "02": "Cavan", "03": "Clare", "04": "Cork", "06": "Donegal",
    "10": "Galway", "11": "Kerry", "12": "Kildare", "13": "Kilkenny", "14": "Leitrim",
    "15": "Laois", "18": "Longford", "19": "Louth", "20": "Mayo", "21": "Meath",
    "22": "Monaghan", "23": "Offaly", "24": "Roscommon", "25": "Sligo", "26": "Tipperary",
    "27": "Waterford", "29": "Westmeath", "30": "Wexford", "31": "Wicklow", "32": "Cork",
    "33": "Dublin", "34": "Dublin", "35": "Dublin", "36": "Galway", "39": "Dublin",
    "42": "Limerick", "44": "Waterford",
}
# Towns, suburbs and villages. Localities (PPLL) are left out: they are mostly
# townlands, and their names ("Park", "Church Hill") turn up in street addresses.
TOWNS = {"PPL", "PPLA", "PPLA2", "PPLC", "PPLX", "PPLS"}


def post_towns(wiki_path: str) -> dict[str, tuple[str, str]]:
    """Each routing key's post town and county, from the Wikipedia table's rows."""
    link = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]")
    found = {}
    with open(wiki_path, encoding="utf-8") as f:
        for row in re.finditer(r"^\|\s*([A-Z][0-9W]{2})\s*\|\|\s*(.+?)\s*\|\|\s*(.+?)\s*$",
                               f.read(), re.M):
            town, county = (link.sub(r"\1", cell).strip() for cell in row.group(2, 3))
            found[row.group(1)] = (town, county)
    return found


def main(zip_path: str, dump_path: str, wiki_path: str) -> None:
    places: dict[str, dict[str, tuple[float, float, int]]] = {}
    with open(dump_path, encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            if row[6] != "P" or row[7] not in TOWNS or row[11] not in COUNTY_CODES:
                continue
            county = COUNTY_CODES[row[11]]
            point = (round(float(row[4]), 4), round(float(row[5]), 4), int(row[14] or 0))
            for name in {fold(row[1]), fold(row[2])}:
                held = places.setdefault(county, {}).get(name)
                # Two places of one name in a county: the bigger one.
                if held is None or point[2] > held[2]:
                    places[county][name] = point

    # Where a role naming only its county is measured from: the county's biggest place,
    # which is where most of its jobs are (Cork city, not the middle of County Cork).
    counties = {
        county: list(max(named.values(), key=lambda p: p[2])[:2])
        for county, named in places.items()
    }
    counties["Dublin"] = list(places["Dublin"]["dublin"][:2])

    towns = post_towns(wiki_path)
    areas = {}
    with open(zip_path, encoding="utf-8") as f:
        for row in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            key, point = row[1], [round(float(row[9]), 4), round(float(row[10]), 4), row[2]]
            town, county = towns.get(key, (row[2], ""))
            # "Swords and Dublin Airport", "Laytown-Bettystown-Mornington": the first.
            for name in (town, re.split(r" and |-(?=[A-Z])", town)[0], row[2]):
                hit = places.get(county, {}).get(fold(name))
                if hit and not key.startswith("D"):
                    point = [hit[0], hit[1], town]
                    break
            areas[key] = point

    # Each post town is a place too, in the county of the town nearest it. GeoNames
    # files a few big towns under their Irish name (Droichead Nua for Newbridge) or not
    # as a populated place at all (Clonmel), and the post town answers for them.
    everywhere = [(county, p) for county, named in places.items() for p in named.values()]
    for key, (lat, lng, town) in areas.items():
        if key.startswith("D") or town.casefold().startswith("cork city"):
            continue
        county = min(everywhere, key=lambda c: distance_km((lat, lng), c[1][:2]))[0]
        places[county].setdefault(fold(town), (lat, lng, 0))

    OUT.write_text(json.dumps({
        "source": "GeoNames (geonames.org), CC BY 4.0",
        "areas": dict(sorted(areas.items())),
        "counties": dict(sorted(counties.items())),
        "places": {
            county: {name: list(p[:2]) for name, p in sorted(named.items())}
            for county, named in sorted(places.items())
        },
    }, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(areas)} routing areas, "
          f"{sum(len(n) for n in places.values())} places in {len(places)} counties -> {OUT}")


if __name__ == "__main__":
    main(*sys.argv[1:4])

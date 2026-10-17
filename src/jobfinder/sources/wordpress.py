"""WordPress vacancy post types.

Many Irish employers' careers pages are WordPress, with vacancies stored as their own
post type (`vacancy`, `job`, WP Job Manager's `job-listings`). WordPress publishes
every public post type through its REST API, paged, with the total in a header:

    GET https://{site}/wp-json/wp/v2/{rest_base}?per_page=100&page=1
        X-WP-Total: 55   X-WP-TotalPages: 1

That is read here instead of scraping the rendered page, which on these sites is
usually built by script. The slug is ``host|rest_base``, e.g.
``musgravegroup.com|vacancy``.

Location is the awkward part: there is no standard field. WP Job Manager keeps it in
`meta._job_location`; other sites file it as a term on the post, or state it in the
advert ("Location: Centra Carrickfergus"). Those are tried in that order, then the
title's last part, which is where Boots writes it ("Seasonal Customer Assistant – Cork,
Mahon Point"), and a site with a fixed location may name it as a third slug part
(``host|rest_base|Dublin, Ireland``).

Terms come from the post's `class_list`: WP Job Openings files a vacancy under
`job-location-templeogue` and `job-type-part-time`, other themes under
`career-location-cork`. The type terms are the hours. A term is a bare town, and
"Monkstown" is in Antrim as well as Dublin, so where the slug names a place its country
is added to the town.

A board for several countries can add ``ireland-only`` as a slug part: boots.jobs lists
about 2,900 roles across the UK and Ireland, and only the Irish ones are kept.

Some sites keep the place in a field the API leaves out, but list every vacancy with it
on their careers page, as data for the page's script. ``places=`` names that page:
``musgravegroup.com|vacancy|places=/careers/vacancies/`` reads the places of all of
Musgrave's 49 roles from one request, where the API gave a place for two of them.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone

import httpx
from dateutil import parser as date_parser

from jobfinder.normalize.location import normalize_location
from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
MAX_PAGES = 30
_TAGS = re.compile(r"<[^>]+>")
_LOCATION = re.compile(r"\bLocation\s*[:\-–]?\s*(?:&nbsp;|\s)*([^\n<]{2,90})", re.I)


IRELAND_ONLY = "ireland-only"
# A BrassRing requisition number before the title: "284513BR: Counter Manager".
_REFERENCE = re.compile(r"^\d{4,}[A-Z]{0,3}\s*:\s*")
# The last part of "Role – Town, Store" or "Role - Town".
_TITLE_PLACE = re.compile(r"\s[–-]\s([^–]+)$")
# A post's location and hours terms in `class_list`: "job-location-bray", "job-type-part-time".
_TERM_PLACE = re.compile(r"^(?:[a-z]+[-_])?locations?[-_]([a-z0-9-]+)$")
_TERM_HOURS = re.compile(r"^(?:job|employment|contract)[-_]types?[-_]([a-z0-9-]+)$")


PLACES = "places="
# One vacancy in a careers page's script data: {"id":31147,"title":...,"location":"Dublin"}.
_LISTED_PLACE = re.compile(r'\{"id":\s*(\d+)\s*,[^{}]*?"location":\s*"((?:[^"\\]|\\.)*)"')


def split_slug(slug: str) -> tuple[str, str, str | None, bool, str | None]:
    host, rest_base, *rest = slug.split("|") + [""]
    if not (host and rest_base):
        raise ValueError(f"WordPress slug must be 'host|rest_base', got {slug!r}")
    places = next((part[len(PLACES):] for part in rest if part.startswith(PLACES)), None)
    place = next((part for part in rest if part and part != IRELAND_ONLY and not part.startswith(PLACES)), None)
    return host, rest_base, place, IRELAND_ONLY in rest, places


def listed_places(page: str) -> dict[str, str]:
    """Post id to place, from the vacancy data a careers page carries for its script."""
    found: dict[str, str] = {}
    for post_id, raw in _LISTED_PLACE.findall(page):
        try:
            value = json.loads(f'"{raw}"')
        except ValueError:
            continue
        value = html.unescape(value).strip()
        if value:
            found.setdefault(post_id, value)
    return found


def _title_place(title: str) -> str | None:
    match = _TITLE_PLACE.search(title)
    if not match:
        return None
    # Only a part that names an Irish place: "Customer Advisor - Weekends" names none,
    # and a board's UK roles are left unplaced, as they were before.
    candidate = match.group(1).strip()
    return candidate if normalize_location(candidate).is_ireland else None


def _terms(item: dict, pattern: re.Pattern[str]) -> list[str]:
    classes = item.get("class_list") or []
    if isinstance(classes, dict):
        classes = list(classes.values())
    found: list[str] = []
    for entry in classes:
        match = pattern.match(entry) if isinstance(entry, str) else None
        if match:
            term = match.group(1).replace("-", " ").title()
            if term not in found:
                found.append(term)
    return found


def _term_place(item: dict, place: str | None) -> str | None:
    towns = _terms(item, _TERM_PLACE)
    if not towns:
        return None
    country = place.rsplit(",", 1)[-1].strip() if place else ""
    return f"{towns[0]}, {country}" if country else towns[0]


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", fragment or ""))).strip()


def _field_location(item: dict) -> str | None:
    for fields in (item.get("meta"), item.get("acf")):
        if not isinstance(fields, dict):
            continue
        for key in ("_job_location", "job_location", "location"):
            value = fields.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _advert_location(item: dict) -> str | None:
    content = html.unescape(_TAGS.sub("\n", (item.get("content") or {}).get("rendered") or ""))
    match = _LOCATION.search(content)
    if not match:
        return None
    return match.group(1).replace("\xa0", " ").strip(" .,:") or None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return date_parser.isoparse(value).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


class WordPressAdapter(BaseAdapter):
    name = "wordpress"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        host, rest_base, place, irish_only, places = split_slug(slug)
        endpoint = f"https://{host}/wp-json/wp/v2/{rest_base}"
        listed: dict[str, str] = {}
        if places:
            # A page that will not load fails the read, rather than leave every role
            # unplaced, and so hidden, until the next one.
            response = client.get(f"https://{host}/{places.lstrip('/')}")
            response.raise_for_status()
            listed = listed_places(response.text)
            if not listed:
                raise ValueError(f"no vacancy places found on {host}/{places.lstrip('/')}")

        items: dict[str, dict] = {}
        total: int | None = None
        for page in range(1, MAX_PAGES + 1):
            response = client.get(endpoint, params={"per_page": PAGE_SIZE, "page": page})
            if page > 1 and response.status_code == 400:
                break  # WordPress answers a page past the end with 400
            response.raise_for_status()
            batch = response.json()
            if not isinstance(batch, list):
                raise ValueError(f"unexpected WordPress payload from {endpoint}")
            if total is None:
                total = int(response.headers.get("X-WP-Total") or len(batch))
                pages = int(response.headers.get("X-WP-TotalPages") or 1)
            for item in batch:
                if item.get("id") is not None and item.get("status", "publish") == "publish":
                    items.setdefault(str(item["id"]), item)
            if page >= pages or not batch:
                break
            self.polite_pause()
        else:
            return PartialJobs(self._build(items, place, irish_only, listed))

        if total and len(items) < total:
            raise ValueError(f"WordPress listed {len(items)} of {total} posts at {endpoint}")
        return self._build(items, place, irish_only, listed)

    @staticmethod
    def _build(
        items: dict[str, dict], place: str | None, irish_only: bool = False,
        listed: dict[str, str] | None = None,
    ) -> list[RawJob]:
        jobs = []
        for job_id, item in items.items():
            title = _REFERENCE.sub("", _text((item.get("title") or {}).get("rendered")))
            if not title:
                continue
            # Fields, listed places and terms are filed by the employer; the advert's
            # "Location" is read from prose, where "due to the location, a car is
            # required" reads as a place.
            location = (_field_location(item) or (listed or {}).get(job_id) or _term_place(item, place)
                        or _advert_location(item) or _title_place(title) or place)
            if irish_only and not normalize_location(location).is_ireland:
                continue
            jobs.append(RawJob(
                source_job_id=job_id,
                title=title,
                url=item.get("link") or "",
                location_raw=location,
                description=(item.get("content") or {}).get("rendered") or None,
                posted_at=_parse_date(item.get("date_gmt") or item.get("date")),
                employment_type=", ".join(_terms(item, _TERM_HOURS)) or None,
            ))
        return jobs


register(WordPressAdapter())

"""Vacancies listed as the rows of a table on a careers page.

Some employers list every vacancy as a row, with no page per role. Homesavers' careers
page has a row for each store's opening, with the county, the role, the store, a job ID,
the hours and the date it was posted, and an Apply link to one shared form:

    County | Role            | Job Location             | Job ID | Type of Job | Job Posted Date
    Athlone| Sales Assistant | Homesavers Diskin Centre | 09-509 | Part Time   | 07-10-2026

`careers_html` looks for links to sibling job pages and opens each one, which here finds
only the form. This reads the rows instead. Columns are recognised by their headings, so
the slug is just the page's URL, and a table without a role column (opening hours, a
price list) is never read as vacancies.

robots.txt is honoured as it is by the other readers of arbitrary pages.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

import httpx
from dateutil import parser as date_parser
from selectolax.parser import HTMLParser, Node

from jobfinder.sources.base import BaseAdapter, RawJob, register
from jobfinder.sources.jsonld import RobotsPolicy
from jobfinder.sources.policy import ExcludedSite, is_excluded

# A column's heading, lower-cased, to the field it holds.
HEADINGS = {
    "title": re.compile(r"^(role|job title|job|position|post|vacancy|title)$"),
    "place": re.compile(r"^(job location|location|store|branch|site|hotel|workplace|town)$"),
    "county": re.compile(r"^(county|region|area)$"),
    "ref": re.compile(r"^(job id|job ref|job reference|ref|reference|ref no|vacancy id|id)$"),
    "hours": re.compile(r"^(type of job|job type|hours|contract|contract type|employment type|type)$"),
    "posted": re.compile(r"^(job posted date|posted date|date posted|posted|posted on)$"),
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _heading(text: str) -> str:
    return _clean(text).lower().rstrip(":").replace(".", "").strip()


def _columns(table: Node) -> dict[str, int] | None:
    """Field to column index, from the table's heading row, or None if it lists no roles."""
    head = table.css_first("thead tr") or table.css_first("tr")
    if head is None:
        return None
    columns: dict[str, int] = {}
    for index, cell in enumerate(head.css("th, td")):
        heading = _heading(cell.text())
        for field, pattern in HEADINGS.items():
            if field not in columns and pattern.match(heading):
                columns[field] = index
                break
    # A role and somewhere to do it, or a reference: a timetable has neither.
    if "title" not in columns or not ({"place", "county", "ref"} & columns.keys()):
        return None
    return columns


def _date(value: str):
    try:
        return date_parser.parse(value, dayfirst=True) if value else None
    except (ValueError, OverflowError):
        return None


def read_tables(html: str, page_url: str) -> tuple[bool, list[RawJob]]:
    """Whether the page has a table of vacancies, and every vacancy in it."""
    found = False
    jobs: dict[str, RawJob] = {}
    for table in HTMLParser(html).css("table"):
        columns = _columns(table)
        if columns is None:
            continue
        found = True
        rows = table.css("tbody tr") or table.css("tr")[1:]
        for row in rows:
            cells = row.css("td, th")

            def cell(field: str) -> str:
                index = columns.get(field)
                return _clean(cells[index].text()) if index is not None and index < len(cells) else ""

            title = cell("title")
            if not title:
                continue
            place, county = cell("place"), cell("county")
            # "Homesavers Diskin Centre, Athlone", but not "Athlone, Athlone". A store
            # named after its town keeps the town as its own part too ("Homesavers Axis
            # Centre Longford, Longford"), since only a part on its own names a place.
            parts = [place] if place else []
            if county and county.lower() != place.rsplit(",", 1)[-1].strip().lower():
                parts.append(county)
            location = ", ".join(parts) or None
            link = row.css_first("a[href]")
            url = urljoin(page_url, link.attributes.get("href") or "") if link is not None else page_url
            ref = cell("ref")
            job_id = ref or (url if url != page_url else f"{title}|{location or ''}")
            jobs.setdefault(job_id, RawJob(
                source_job_id=job_id,
                title=title[:300],
                url=url,
                location_raw=location,
                posted_at=_date(cell("posted")),
                employment_type=cell("hours") or None,
            ))
    return found, list(jobs.values())


class VacancyTableAdapter(BaseAdapter):
    name = "vacancy_table"
    tier = 3

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        page_url = slug if "//" in slug else f"https://{slug}"
        if is_excluded(page_url):
            raise ExcludedSite(f"{page_url} is on a site this project does not crawl")
        parts = urlsplit(page_url)
        if not RobotsPolicy(f"{parts.scheme}://{parts.netloc}", client).allows(page_url):
            raise PermissionError(f"robots.txt disallows {page_url}")

        response = client.get(page_url)
        response.raise_for_status()
        found, jobs = read_tables(response.text, str(response.url))
        if not found:
            # A page whose table has gone is a broken read, not an employer with nothing
            # open: failing keeps the last good list rather than closing every role. An
            # empty table is nothing open.
            raise ValueError(f"no table of vacancies found at {page_url}")
        return jobs


register(VacancyTableAdapter())

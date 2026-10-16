"""BESTSELLER careers adapter: Jack & Jones, Vero Moda, Only, Vila, Name It, Selected.

BESTSELLER lists every brand's vacancies, in every country, from one endpoint behind its
own careers site:

    GET https://bestseller.com/api/v1/jobs/GetJobs
        -> "[{Url, JobTitle, JobDepartment, JobCountry: "Tullamore, IE"}, ...]"

The answer is a JSON list sent as a JSON string, so it is decoded twice. Each entry ends
its place with the country code, and the Irish ones are kept. A role's own page is
rendered on the server with the advert under `itemprop="description"`, the posting date
in its microdata and the hours in its metadata (`item_type` "Part time"), so each Irish
role is read once more for those.

The slug is the country code, ``IE``.
"""

from __future__ import annotations

import json
import logging
import re

import httpx
from dateutil import parser as date_parser
from selectolax.parser import HTMLParser

from jobfinder.sources.base import BaseAdapter, RawJob, register

logger = logging.getLogger(__name__)

SITE = "https://bestseller.com"
JOBS_URL = f"{SITE}/api/v1/jobs/GetJobs"
JOB_ID = re.compile(r"/(\d+)/?$")


def parse_jobs(payload) -> list[dict]:
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, list):
        raise ValueError("BESTSELLER job list was not a list - endpoint changed")
    return [entry for entry in payload if isinstance(entry, dict)]


def read_advert(html: str) -> dict:
    tree = HTMLParser(html)
    advert = tree.css_first('[itemprop="description"]')
    posted = tree.css_first('meta[itemprop="datePosted"]')
    hours = tree.css_first('meta-data-item[name="item_type"]')
    try:
        posted_at = date_parser.parse(posted.attributes.get("content")) if posted else None
    except (ValueError, OverflowError, TypeError):
        posted_at = None
    return {
        "description": advert.html if advert is not None else None,
        "posted_at": posted_at,
        "employment_type": (hours.attributes.get("value") or None) if hours is not None else None,
    }


def _place(country_field: str, code: str) -> str:
    """'Tullamore, IE' -> 'Tullamore, Ireland'."""
    town = country_field.rsplit(",", 1)[0].strip()
    return f"{town}, Ireland" if code == "IE" and town else town


class BestsellerAdapter(BaseAdapter):
    name = "bestseller"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        code = (slug or "IE").strip().upper()
        response = client.get(JOBS_URL)
        response.raise_for_status()
        entries = parse_jobs(response.json())
        # Hundreds of roles across the brands' countries are open at any time, so an
        # empty list is a broken endpoint, not a closed board.
        if not entries:
            raise ValueError("BESTSELLER listed no jobs anywhere - endpoint changed")

        jobs: list[RawJob] = []
        for entry in entries:
            where = (entry.get("JobCountry") or "").strip()
            path = entry.get("Url") or ""
            found = JOB_ID.search(path)
            if not where.upper().endswith(f", {code}") or not found:
                continue
            url = f"{SITE}{path}" if path.startswith("/") else path
            advert: dict = {}
            try:
                self.polite_pause()
                page = client.get(url)
                if page.status_code == 200:
                    advert = read_advert(page.text)
            except httpx.HTTPError as exc:
                logger.info("bestseller: advert %s did not load: %r", url, exc)
            jobs.append(RawJob(
                source_job_id=found.group(1),
                title=(entry.get("JobTitle") or "").strip(),
                url=url,
                location_raw=_place(where, code),
                description=advert.get("description"),
                posted_at=advert.get("posted_at"),
                department=entry.get("JobDepartment") or None,
                employment_type=advert.get("employment_type"),
            ))
        return jobs


register(BestsellerAdapter())

"""Harri careers pages.

    GET  https://gateway.harri.com/core/api/v1/profile/slug/{slug}
    POST https://gateway.harri.com/core/api/v1/harri_search/search_jobs
    GET  https://gateway.harri.com/core-reader/api/v1/profile/job/{id}

Harri, an ATS built for restaurants and hotels, draws each careers page at
`harri.com/{slug}` from a public gateway. The slug names a brand, one shop or hotel
department, or a group of them; the search takes its id and gives every role published
under it, with the shop, its address and the pay, two hundred at a time beside the total.
Each role's own record carries the advert.

Groups run across borders. KFC's holds some 900 roles in Great Britain and Ireland, and
its Dublin shops sit in an area group named after their manager, so a group is read
whole and only the roles placed in the Republic are kept. Their addresses decide that, as
every other place is decided, not Harri's country codes: Crew Clothing's Dundrum Town
Centre shop is coded UK beside its Eircode, and KFC's Belfast roles are coded IE.

The slug is a brand's or group's page slug, or its numeric id where the slug is not one
to keep (KFC (Great Britain) Limited's is a run of random letters).
"""

from __future__ import annotations

import logging
import re

import httpx
from dateutil import parser as date_parser

from jobfinder.normalize.location import normalize_location
from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

logger = logging.getLogger(__name__)

GATEWAY = "https://gateway.harri.com"
PROFILE = GATEWAY + "/core/api/v1/profile/slug/{slug}"
SEARCH = GATEWAY + "/core/api/v1/harri_search/search_jobs"
RECORD = GATEWAY + "/core-reader/api/v1/profile/job/{job_id}"
JOB_PAGE = "https://harri.com/{brand}/job/{job_id}-{title}"

PAGE_SIZE = 200
MAX_PAGES = 25
MAX_DESCRIPTIONS = 60

PERIODS = {"PER_HOUR": "an hour", "PER_DAY": "a day", "PER_WEEK": "a week",
           "PER_MONTH": "a month", "PER_YEAR": "a year"}


def _clean(text: str | None) -> str:
    """"Unit 7, Level 2\\tDundrum Town Centre, , D16" without its tab or empty part."""
    parts = (re.sub(r"\s+", " ", part).strip() for part in (text or "").split(","))
    return ", ".join(part for part in parts if part)


def _place(location: dict) -> str | None:
    return _clean(location.get("formatted_address")) or _clean(
        ", ".join(str(location.get(key) or "") for key in ("city", "country"))
    ) or None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _amount(value) -> str:
    return f"{value:,.2f}".removesuffix(".00")


def _pay(compensation: dict | None) -> str | None:
    """"€21.33 to €21.65 an hour". Only Irish roles are kept, so the pay is in euro."""
    if not isinstance(compensation, dict):
        return None
    amounts = [f"€{_amount(value)}" for value in
               (compensation.get("compensation_from"), compensation.get("compensation_to"))
               if isinstance(value, (int, float)) and value > 0]
    if not amounts:
        return None
    if len(amounts) == 2 and amounts[0] == amounts[1]:
        amounts = amounts[:1]
    period = PERIODS.get((compensation.get("rate") or {}).get("code") or "", "")
    return " ".join(part for part in (" to ".join(amounts), period) if part)


def _labels(values) -> list[str]:
    """`employment_types` as "Part time", whether codes or named records."""
    labels: list[str] = []
    for value in values or []:
        if isinstance(value, dict):
            value = value.get("name") or value.get("code")
        if isinstance(value, str) and value.strip():
            labels.append(value.strip().replace("_", " ").capitalize())
    return labels


def _date(value):
    try:
        return date_parser.parse(value) if value else None
    except (ValueError, TypeError, OverflowError):
        return None


class HarriAdapter(BaseAdapter):
    name = "harri"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        brand_id = self._brand_id(slug, client)
        results: list[dict] = []
        hits = 0
        for page in range(MAX_PAGES):
            if page:
                self.polite_pause()
            response = client.post(SEARCH, json={
                "size": PAGE_SIZE, "start": len(results), "source": "web",
                "brand_level_ids": [brand_id], "sort": ["publish_date"], "sort_type": "desc",
                "flow": "CAREER_PORTAL",
            })
            response.raise_for_status()
            data = (response.json() or {}).get("data")
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError(f"unexpected Harri search answer for {slug!r}")
            hits = int(data.get("hits") or 0)
            results.extend(data["results"])
            if not data["results"] or len(results) >= hits:
                break

        jobs: list[RawJob] = []
        for item in results:
            job_id = item.get("id")
            title = (item.get("aliasPosition") or (item.get("position") or {}).get("name") or "").strip()
            places = [place for place in map(_place, item.get("locations") or []) if place]
            irish = [place for place in places if normalize_location(place).is_ireland]
            if not job_id or not title or not irish:
                continue
            brand = item.get("brand") or {}
            record = self._record(job_id, client) if len(jobs) < MAX_DESCRIPTIONS else {}
            hours = record.get("weekly_work_time")
            details = [
                f"<p>{label}: {value}</p>" for label, value in (
                    ("Pay", _pay(item.get("compensation"))),
                    ("Hours", f"{_amount(hours)} hours a week" if isinstance(hours, (int, float)) else None),
                ) if value
            ]
            jobs.append(
                RawJob(
                    source_job_id=str(job_id),
                    title=title,
                    url=JOB_PAGE.format(brand=brand.get("slug") or slug, job_id=job_id, title=_slug(title)),
                    location_raw=irish[0],
                    extra_locations=irish[1:],
                    description="".join([record.get("description") or "", *details]) or None,
                    posted_at=_date(item.get("publishTime")),
                    # The shop or hotel department the role is published under.
                    department=(brand.get("name") or "").strip() or None,
                    employment_type=", ".join(_labels(record.get("employment_types"))) or None,
                )
            )
        return PartialJobs(jobs) if len(results) < hits else jobs

    def _brand_id(self, slug: str, client: httpx.Client) -> int:
        if slug.isdigit():
            return int(slug)
        response = client.get(PROFILE.format(slug=slug))
        if response.status_code == 400:
            # "Slug not found": Hawksmoor Dublin's own page went this way, its roles now
            # under the group's.
            raise ValueError(f"no Harri page named {slug!r}")
        response.raise_for_status()
        profile = (response.json() or {}).get("data") or {}
        if not isinstance(profile, dict) or not profile.get("id"):
            raise ValueError(f"unexpected Harri profile for {slug!r}")
        if profile.get("is_archived"):
            # Wingstop Ireland's brand was archived when its shops moved to the UK group.
            raise ValueError(f"Harri page {slug!r} is archived")
        return int(profile["id"])

    def _record(self, job_id, client: httpx.Client) -> dict:
        self.polite_pause()
        try:
            response = client.get(RECORD.format(job_id=job_id))
            response.raise_for_status()
            record = ((response.json() or {}).get("data") or {}).get("job") or {}
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            logger.debug("harri: no record for %s: %r", job_id, exc)
            return {}
        return record if isinstance(record, dict) else {}


register(HarriAdapter())

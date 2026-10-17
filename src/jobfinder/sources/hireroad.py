"""HireRoad careers pages.

    GET https://api.hireroadrecruit.com/api/public/careers/{slug}/jobs?page={n}&limit=100
    GET https://api.hireroadrecruit.com/api/public/careers/{slug}/jobs/{id}

A HireRoad careers page, `app.hireroadrecruit.com/careers/{slug}`, is an app drawn from a
public JSON API. The list gives each open role's title, place, contract and pay, a page
at a time beside the total, and the read is checked against that total. The advert text
is on each role's own record, read as Breezy's descriptions are: capped, because a
description is worth having but not worth an unbounded crawl.

Hours are where the customer puts them. A role may carry a working-hours label
(`part_time`, `zero_hours`) and its hours a week; Regatta leaves both empty and writes
"Sales Assistant - 8 Hours Per Week" in the title instead.

Regatta files its Irish shops under a company of their own, `regatta-ireland`, beside
the UK one. Its careers site shows both on a group board, `regatta/group`, so the slug is
the Irish company's rather than the one the site links to.
"""

from __future__ import annotations

import logging

import httpx
from dateutil import parser as date_parser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register

logger = logging.getLogger(__name__)

API = "https://api.hireroadrecruit.com/api/public/careers/{slug}"
JOB_PAGE = "https://app.hireroadrecruit.com/careers/{slug}/jobs/{job_id}"

# The API refuses a page of more than 100.
PAGE_SIZE = 100
MAX_PAGES = 10
MAX_DESCRIPTIONS = 60

CURRENCIES = {"EUR": "€", "GBP": "£", "USD": "$"}
PERIODS = {"hourly": "an hour", "daily": "a day", "weekly": "a week", "monthly": "a month",
           "annual": "a year", "yearly": "a year"}


def _label(value) -> str | None:
    """`fixed_term` as "Fixed term"."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().replace("_", " ").capitalize()


def _amount(value) -> str:
    return f"{value:,.2f}".removesuffix(".00") if isinstance(value, (int, float)) else str(value)


def _pay(item: dict) -> str | None:
    """"€14.15 an hour", "€20,000 to €24,000 a year"."""
    low, high = item.get("salaryMin"), item.get("salaryMax")
    if low is None and high is None:
        return None
    sign = CURRENCIES.get(item.get("salaryCurrency") or "", "")
    amounts = [f"{sign}{_amount(value)}" for value in (low, high) if value is not None]
    if len(amounts) == 2 and amounts[0] == amounts[1]:
        amounts = amounts[:1]
    period = PERIODS.get(item.get("salaryPeriod") or "", "")
    code = "" if sign else f" {item.get('salaryCurrency') or ''}".rstrip()
    return " ".join(part for part in (" to ".join(amounts) + code, period) if part)


def _details(item: dict) -> str | None:
    hours = item.get("hoursPerWeek")
    lines = [
        f"{label}: {value}" for label, value in (
            ("Pay", _pay(item)),
            ("Hours", f"{_amount(hours)} hours a week" if hours else None),
            ("Contract", _label(item.get("employmentType"))),
        ) if value
    ]
    return "".join(f"<p>{line}</p>" for line in lines) or None


def _date(value):
    try:
        return date_parser.parse(value) if value else None
    except (ValueError, TypeError, OverflowError):
        return None


class HireRoadAdapter(BaseAdapter):
    name = "hireroad"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        api = API.format(slug=slug)
        items: list[dict] = []
        page, total = 1, 0
        while True:
            response = client.get(f"{api}/jobs", params={"page": page, "limit": PAGE_SIZE})
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
                raise ValueError(f"unexpected HireRoad payload for {slug!r}")
            items.extend(data["jobs"])
            total = int(data.get("total") or 0)
            if page >= int(data.get("totalPages") or 1) or not data["jobs"]:
                break
            if page >= MAX_PAGES:
                break
            page += 1
            self.polite_pause()

        jobs: list[RawJob] = []
        for index, item in enumerate(items):
            job_id = item.get("id")
            title = (item.get("title") or "").strip()
            if not job_id or not title:
                continue
            advert = self._description(api, job_id, client) if index < MAX_DESCRIPTIONS else None
            details = _details(item)
            hours = ", ".join(
                label for label in (_label(item.get("jobTime")), _label(item.get("employmentType")))
                if label
            )
            jobs.append(
                RawJob(
                    source_job_id=str(job_id),
                    title=title,
                    url=JOB_PAGE.format(slug=slug, job_id=job_id),
                    location_raw=(item.get("location") or "").strip() or None,
                    description="".join(part for part in (advert, details) if part) or None,
                    posted_at=_date(item.get("publishedAt")),
                    department=(item.get("department") or {}).get("name")
                    if isinstance(item.get("department"), dict) else item.get("department"),
                    employment_type=hours or None,
                )
            )
        return PartialJobs(jobs) if len(items) < total else jobs

    def _description(self, api: str, job_id: str, client: httpx.Client) -> str | None:
        try:
            response = client.get(f"{api}/jobs/{job_id}")
            response.raise_for_status()
            data = response.json().get("data") or {}
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            logger.debug("hireroad: no description for %s: %r", job_id, exc)
            return None
        finally:
            self.polite_pause()
        return data.get("description") or None


register(HireRoadAdapter())

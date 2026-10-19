"""Jobtrain careers sites.

    GET https://{host}/Home/_JobCard?Skip={n}     the list, twelve cards at a time
    GET https://{host}/Job/JobDetail?JobId={id}   each role's advert and date

Jobtrain, a UK recruitment system, runs the careers sites of Irish public bodies such as
Cavan and Monaghan ETB (careers.cmetb.ie) and Louth and Meath ETB (careers.lmetb.ie).
Each card gives the role's title, place, contract type and weekly hours,

    <a href="/Job/JobDetail?JobId=615">Temporary, Full-time Caretaker in Beech Hill College</a>
    <p class="jobdetailsitem employmenttype"><strong>Contract type:</strong> Fixed-Term, Full-Time</p>

and the list says how many roles match in all (`totalMatchRecords`), so a short read is
seen for what it is. The advert and date come from each role's own page, whose
JobPosting markup is typed `application/ld&#x2B;json` and so is read for its date alone.

The slug is the site's host, `host|place=WHERE`, the place being given only to roles
whose card names nowhere in Ireland ("CMETB's Further Education and Training Service").
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

import httpx
from dateutil import parser as date_parser
from selectolax.parser import HTMLParser, Node

from jobfinder.normalize.location import normalize_location
from jobfinder.sources.base import BaseAdapter, RawJob, register
from jobfinder.sources.jsonld import RobotsPolicy

logger = logging.getLogger(__name__)

PAGE_SIZE = 12
MAX_PAGES = 25
MAX_ADVERTS = 80
TOTAL = re.compile(r'id="totalMatchRecords"\s+value="(\d+)"')
POSTED = re.compile(r'"datePosted"\s*:\s*"([^"]+)"')
ADVERT = "#Media_Rich_Advert"


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _item(card: Node, name: str) -> str | None:
    """A card detail's value without its label: "Fixed-Term, Full-Time"."""
    node = card.css_first(f"p.jobdetailsitem.{name}")
    if node is None:
        return None
    label = node.css_first("strong")
    text = _clean(node.text())
    if label is not None:
        text = _clean(text.removeprefix(_clean(label.text())))
    return text or None


def read_cards(html: str, base: str, place: str | None = None) -> tuple[int | None, list[RawJob]]:
    """How many roles match in all, and a role for each card on this page."""
    total = TOTAL.search(html)
    jobs: list[RawJob] = []
    for card in HTMLParser(html).css("div.job-card"):
        link = card.css_first("a[href*='JobDetail']")
        if link is None:
            continue
        url = urljoin(base, link.attributes.get("href") or "")
        job_id = re.search(r"JobId=(\d+)", url)
        title = _clean(link.text())
        if not job_id or not title:
            continue
        where = card.css_first(".job-card__location")
        location = _clean(where.text()).removeprefix("location_on").strip() if where is not None else ""
        if place and not normalize_location(location).is_ireland:
            location = f"{location}, {place}" if location else place
        contract = _item(card, "employmenttype")
        hours = _item(card, "hours")
        jobs.append(RawJob(
            source_job_id=job_id.group(1),
            title=title[:300],
            url=url,
            location_raw=location or None,
            department=_item(card, "department"),
            employment_type=", ".join(filter(None, (contract, f"{hours} hours a week" if hours else None))) or None,
        ))
    return (int(total.group(1)) if total else None), jobs


class JobtrainAdapter(BaseAdapter):
    name = "jobtrain"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        host, *options = slug.split("|")
        place = next((value for key, _, value in (o.partition("=") for o in options) if key == "place"), None)
        base = f"https://{host}"
        robots = RobotsPolicy(base, client)

        jobs: dict[str, RawJob] = {}
        total = None
        for page in range(MAX_PAGES):
            response = client.get(f"{base}/Home/_JobCard", params={"Skip": page * PAGE_SIZE})
            response.raise_for_status()
            count, cards = read_cards(response.text, base, place)
            if count is None:
                raise ValueError(f"no Jobtrain list at {base}")
            total = count
            for job in cards:
                jobs.setdefault(job.source_job_id, job)
            if not cards or len(jobs) >= total:
                break
            self.polite_pause()
        if total and len(jobs) < total:
            raise ValueError(f"read {len(jobs)} of {total} roles on {base}")

        for job in list(jobs.values())[:MAX_ADVERTS]:
            if robots.allows(job.url):
                self._advert(job, client)
        return list(jobs.values())

    def _advert(self, job: RawJob, client: httpx.Client) -> None:
        self.polite_pause()
        try:
            response = client.get(job.url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.debug("jobtrain: %s unreachable: %r", job.url, exc)
            return
        advert = HTMLParser(response.text).css_first(ADVERT)
        if advert is not None:
            job.description = advert.html
        posted = POSTED.search(response.text)
        if posted:
            try:
                job.posted_at = date_parser.parse(posted.group(1))
            except (ValueError, OverflowError):
                pass


register(JobtrainAdapter())

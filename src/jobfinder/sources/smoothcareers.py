"""Smooth Careers lists on Shopify shops.

    GET {careers page}          the list: a card per role
    GET {shop}/pages/{role}     each role's page: its advert and a form to apply

Smooth Careers (careers.smooth.ie), an Irish app, writes a Shopify shop's careers pages.
The list's cards give each role's title, store, hours and team,

    <a href="/pages/temporary-sales-advisor-16">
      <li class="smoothie-job-listing-card" location="Lucan" job-type="FULL_TIME"
          team="Sales"><h3>Temporary Sales Advisor</h3> ...

and each role's own page carries the advert, with `JobPosting` markup. The cards are the
list, so every role is read from its card, and its page adds the advert and the date.
On 17 Oct 2026 seven of DID Electrical's nineteen role pages showed the shop's About page
in place of their advert while their cards stayed up. Such a role links to the list,
where its card is, rather than to a page that says nothing of it.

The slug is the careers page: the list is one page of the shop, not the whole site.
`page|place=WHERE` gives a one-venue employer's place to cards that name none, as
Monroe's in Galway leaves its cards' location blank. robots.txt is honoured as it is by the other readers of an employer's own pages.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlsplit

import httpx
from dateutil import parser as date_parser
from selectolax.parser import HTMLParser, Node

from jobfinder.sources.base import BaseAdapter, RawJob, register
from jobfinder.sources.jsonld import RobotsPolicy, _is_job_posting, iter_ld_objects, parse_job_posting
from jobfinder.sources.policy import ExcludedSite, is_excluded

logger = logging.getLogger(__name__)

CARD = "li.smoothie-job-listing-card"
LIST = "#smoothie-search-list"
ADVERT = ".smoothie-job-editor-content"
MAX_ADVERTS = 60

# "3 Oct 2026", the date some cards show among their details.
CARD_DATE = re.compile(r"^\d{1,2} [A-Za-z]{3,9} \d{4}$")
WEB_ADDRESS = re.compile(r"https?://\S+")


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _label(value: str | None) -> str | None:
    """`PART_TIME` as "Part time"."""
    value = _clean(value)
    return value.replace("_", " ").capitalize() if value else None


def _card_date(card: Node):
    for detail in card.css(".detail-text"):
        text = _clean(detail.text())
        if CARD_DATE.match(text):
            try:
                return date_parser.parse(text, dayfirst=True)
            except (ValueError, OverflowError):
                return None
    return None


def read_list(html: str, page_url: str, place: str | None = None) -> tuple[bool, list[RawJob]]:
    """Whether the page holds a Smooth Careers list, and a role for each of its cards."""
    tree = HTMLParser(html)
    cards = tree.css(CARD)
    jobs: dict[str, RawJob] = {}
    for card in cards:
        parent = card.parent
        link = parent if parent is not None and parent.tag == "a" else card.css_first("a[href]")
        href = (link.attributes.get("href") or "").strip() if link is not None else ""
        heading = card.css_first("h1, h2, h3, h4, h5")
        title = _clean(heading.text()) if heading is not None else ""
        if not href or not title:
            continue
        url = urljoin(page_url, href).split("#")[0]
        # DID's warehouse card carries the app's own edit link after its team.
        team = _clean(WEB_ADDRESS.sub("", card.attributes.get("team") or ""))
        jobs.setdefault(url, RawJob(
            source_job_id=url,
            title=title[:300],
            url=url,
            location_raw=_clean(card.attributes.get("location")) or place,
            posted_at=_card_date(card),
            department=team or None,
            employment_type=_label(card.attributes.get("job-type")),
        ))
    return bool(cards) or tree.css_first(LIST) is not None, list(jobs.values())


class SmoothCareersAdapter(BaseAdapter):
    name = "smoothcareers"
    tier = 3

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        slug, _, place = slug.partition("|place=")
        page_url = slug if "//" in slug else f"https://{slug}"
        if is_excluded(page_url):
            raise ExcludedSite(f"{page_url} is on a site this project does not crawl")
        parts = urlsplit(page_url)
        robots = RobotsPolicy(f"{parts.scheme}://{parts.netloc}", client)
        if not robots.allows(page_url):
            raise PermissionError(f"robots.txt disallows {page_url}")

        response = client.get(page_url)
        response.raise_for_status()
        listing = str(response.url)
        found, jobs = read_list(response.text, listing, place or None)
        if not found:
            # A page whose list has gone is a broken read, not a shop with nothing open:
            # failing keeps the last good list rather than closing every role.
            raise ValueError(f"no Smooth Careers list found at {page_url}")
        # Every role is on the list whether or not its advert is read, so a cap on the
        # adverts never makes the list partial.
        for job in jobs[:MAX_ADVERTS]:
            if robots.allows(job.url):
                self._advert(job, listing, client)
        return jobs

    def _advert(self, job: RawJob, listing: str, client: httpx.Client) -> None:
        self.polite_pause()
        try:
            response = client.get(job.url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # Unread is not gone: the role keeps its own page as its link.
            logger.debug("smoothcareers: %s unreachable: %r", job.url, exc)
            return
        advert = HTMLParser(response.text).css_first(ADVERT)
        marked = next(
            (parse_job_posting(obj, job.url) for obj in iter_ld_objects(response.text)
             if _is_job_posting(obj)),
            None,
        )
        if advert is None and marked is None:
            job.url = listing
            return
        job.description = (advert.html if advert is not None else None) or (
            marked.description if marked is not None else None
        )
        if job.posted_at is None and marked is not None:
            job.posted_at = marked.posted_at


register(SmoothCareersAdapter())

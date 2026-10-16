"""PostingPanda (Talos360) store job boards, such as Domino's storejobs.dominos.co.uk.

    GET https://{host}/AllJobs?LocationCountry={country}&per_page=500&page={n}
    GET https://{host}/jobdetails?jobPost={id}

Domino's stores in Ireland and the UK are run by franchisees, and all of them advertise
on the one store jobs board. Its list is server-rendered: one card per vacancy with the
title, the store's place, the contract and the pay, and the list states its own size
("Showing 107 results"). The country filter covers the island, not the state, so a card
is kept only where its place reads as the Republic: the Coalisland and Belfast stores
come back under "Ireland" too.

Each kept vacancy's page carries JobPosting markup with the advert and the hours
(`PART_TIME`), so it is read for those. A page that will not load still leaves the
card's title, place and contract.

The slug is ``host|country``: ``storejobs.dominos.co.uk|Ireland``.
"""

from __future__ import annotations

import logging
import re

import httpx
from selectolax.parser import HTMLParser

from jobfinder.normalize.location import normalize_location
from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register
from jobfinder.sources.jsonld import _is_job_posting, iter_ld_objects, parse_job_posting

logger = logging.getLogger(__name__)

PAGE_SIZE = 500
MAX_PAGES = 10
TOTAL = re.compile(r"Showing\s+([\d,]+)\s+results", re.I)
JOB_ID = re.compile(r"jobPost=(\d+)")


def _field(card, alt: str) -> str | None:
    """The text beside one of a card's icons: Location, clock (contract), salary."""
    icon = card.css_first(f'img[alt="{alt}"]')
    paragraph = icon.parent.css_first("p") if icon is not None and icon.parent else None
    text = " ".join(paragraph.text().split()) if paragraph is not None else ""
    return text or None


def parse_list(html: str) -> tuple[list[dict], int | None]:
    """Every card on a list page, and the size the list states."""
    tree = HTMLParser(html)
    # Each title and place carries its own <style> block, whose rules would otherwise
    # be read as part of the text.
    for style in tree.css("style"):
        style.decompose()

    cards: list[dict] = []
    seen: set[str] = set()
    for link in tree.css('h3 a[href*="jobPost="]'):
        found = JOB_ID.search(link.attributes.get("href") or "")
        title = " ".join(link.text().split())
        if not found or not title or found.group(1) in seen:
            continue
        seen.add(found.group(1))
        card = link.parent.parent if link.parent is not None else None
        if card is None:
            continue
        cards.append({
            "id": found.group(1),
            "title": title,
            "place": _field(card, "Location"),
            "contract": _field(card, "clock"),
        })

    total = TOTAL.search(html)
    return cards, int(total.group(1).replace(",", "")) if total else None


def read_advert(html: str, url: str) -> RawJob | None:
    for obj in iter_ld_objects(html):
        if _is_job_posting(obj):
            return parse_job_posting(obj, url)
    return None


class PostingPandaAdapter(BaseAdapter):
    name = "postingpanda"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        host, _, country = slug.partition("|")
        host = host.strip()
        country = country.strip() or "Ireland"

        cards: list[dict] = []
        total: int | None = None
        for page in range(1, MAX_PAGES + 1):
            response = client.get(
                f"https://{host}/AllJobs",
                params={"LocationCountry": country, "location": "", "radius": 25,
                        "page": page, "per_page": PAGE_SIZE},
            )
            response.raise_for_status()
            found, stated = parse_list(response.text)
            if stated is None:
                raise ValueError(f"no result count on {host}'s job list")
            total = stated
            cards.extend(found)
            if not found or len(cards) >= total:
                break
            self.polite_pause()

        jobs: list[RawJob] = []
        for card in cards:
            if not normalize_location(card["place"]).is_ireland:
                continue
            url = f"https://{host}/jobdetails?jobPost={card['id']}"
            advert = None
            try:
                self.polite_pause()
                page = client.get(url)
                if page.status_code == 200:
                    advert = read_advert(page.text, url)
            except httpx.HTTPError as exc:
                logger.info("postingpanda: advert %s did not load: %r", url, exc)
            jobs.append(RawJob(
                source_job_id=card["id"],
                title=card["title"],
                url=url,
                location_raw=card["place"],
                description=advert.description if advert else None,
                posted_at=advert.posted_at if advert else None,
                employment_type=(advert.employment_type if advert else None) or card["contract"],
            ))

        # A list read short of its own stated size is a sample: the roles not read are
        # not closed.
        if total is not None and len(cards) < total:
            return PartialJobs(jobs)
        return jobs


register(PostingPandaAdapter())

"""Vacancies written out on a careers page, each under its own heading.

Many small chains list their openings on the careers page itself, with no page per
role and an email address or a form to apply:

    <h2>Manager – Drumcondra, Dublin</h2>
    <p>We are looking for an experienced and self-motivated manager ...</p>
    <p>Please forward CV and a cover letter to careers@avisbudget.ie</p>
    <h2>Sales Rental Agent – Galway City</h2> ...

`careers_html` follows links to job pages and finds none here. This reads the headings
instead: each one that reads as a role, or names its hours ("Leopardstown Store - Full
Time"), with the text under it up to the next heading of its rank. A page's roles share
one rank, the one most role-like headings have: Soundstore heads each role with an
<h1> and its parts with <h3>s, and its "Position: Retail Sales Consultants" <h3> is part
of a role, not another. A page with no heading for its roles, Modern Tyres', may give
each a bold line instead, and those are read when nothing else is.

A role's place is the one its title names, else one in its first short lines
("Full-time · Galway"), else the one named by a heading above the roles' rank (Horans
heads each shop's roles with its town). Every heading of that rank starts afresh, so
McGuirks' "ULSTER" does not inherit "CONNACHT". A role placed nowhere takes the slug's
`place=`, given only for an employer hiring in Ireland alone as `jsonld`'s is, and
otherwise is left for the pipeline to judge from its advert. A place in the North
("Rental Sales Agent – Belfast Airports") is kept as named, so no fallback moves the
role into the Republic.

The slug is the page, `url|place=WHERE`. It is set by hand, never by detection: a
careers brochure's "Our roles" section reads just like a list of vacancies. Each role
links to the page, scrolled to its heading where the browser supports text fragments.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from urllib.parse import quote, urlsplit

import httpx
from selectolax.parser import HTMLParser, Node

from jobfinder.normalize.location import _NORTHERN_PLACES, _NORTHERN_SIGNAL, normalize_location
from jobfinder.sources.base import BaseAdapter, RawJob, register
from jobfinder.sources.careers_html import _reads_as_role
from jobfinder.sources.jsonld import RobotsPolicy, split_slug
from jobfinder.sources.policy import ExcludedSite, is_excluded

RANKS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
BOLD = 7
# A card's title by its class, as Kelly's Resort Hotel writes each role:
# <p class="title">Waiter/Waitress</p><p class="subtitle">Full Time</p>.
CARD = 8
# Form controls, not forms: Modern Tyres' and McGuirks' pages sit wholly inside one.
DROPPED = "script, style, noscript, header, footer, nav, svg, select, button, input, textarea, template"
BLOCKS = {
    "p", "div", "li", "ul", "ol", "td", "th", "tr", "table", "dd", "dt", "dl", "section",
    "article", "aside", "blockquote", "figure", "figcaption", "pre", "address", "main",
    "body", *RANKS,
}

HOURS = re.compile(r"\b(?:full|part)[- ]?time\b", re.I)
# What any careers page says somewhere. A page saying none of it is not the careers page
# any more, and reading it as one with nothing open would close every role.
CAREERS_WORDS = re.compile(
    r"\bappl(?:y|ication)|\bC\.?V\b|curriculum vitae|\bvacanc|\bcareers?\b|\brecruit"
    r"|\bhiring\b|\bjoin (?:our|the) team\b",
    re.I,
)
# Where a title or a short line breaks into parts: "Retail Support - Tralee, Part-Time".
PARTS = re.compile(r"\s+[-–—]\s+|[(),·|:/]")
# A place part longer than this is a phrase ("Join the team in Waterford"), named by
# its county instead.
PLACE_WORDS = 4
# The North's towns inside a part, as "Belfast Store" names one; the location module
# matches them only as a whole place. "Down" is left out, a word as often as a county.
NORTH = re.compile(r"\b(?:" + "|".join(sorted(_NORTHERN_PLACES - {"down"})) + r")\b", re.I)
DETAIL_LINES = 3
DETAIL_LENGTH = 80


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _is_role(text: str) -> bool:
    if not 4 <= len(text) <= 150:
        return False
    return _reads_as_role(text) or bool(HOURS.search(text))


def _place(text: str) -> str | None:
    """The places in the Republic a title or a short line names, as it names them, or
    failing those the place it names in the North."""
    irish: list[str] = []
    north: str | None = None
    for part in PARTS.split(text):
        part = part.strip(" .")
        if not part:
            continue
        where = normalize_location(part, company_is_irish=True)
        if where.is_ireland and where.region:
            irish.append(part if len(part.split()) <= PLACE_WORDS else f"{where.region}, Ireland")
        elif north is None and (NORTH.search(part) or _NORTHERN_SIGNAL.search(part)):
            north = part
    return ", ".join(irish) if irish else north


def _stands_alone(node: Node) -> bool:
    """A bold line that is the whole of its paragraph, as a heading would be."""
    block = node.parent
    while block is not None and block.tag not in BLOCKS:
        block = block.parent
    text = _clean(node.text(separator=" "))
    return bool(text) and block is not None and _clean(block.text(separator=" ")) == text


def _card_title(node: Node) -> bool:
    names = (node.attributes.get("class") or "").lower().split()
    return any(name.endswith("title") and not name.endswith("subtitle") for name in names)


def _items(root: Node, mode: int) -> list[tuple[int, str]]:
    """(rank, text) for each heading and (0, text) for each line, in reading order."""
    items: list[tuple[int, str]] = []
    headings: set[int] = set()
    line: list[str] = []
    block_id: int | None = None

    def flush() -> None:
        text = _clean(" ".join(line))
        # A widget's settings left in a hidden div, as Horans' page has after its
        # last role, are not a line of it.
        if text and not (text.startswith("{") and text.endswith("}")):
            items.append((0, text))
        line.clear()

    for node in root.traverse(include_text=True):
        rank = RANKS.get(node.tag) or (
            BOLD if mode == BOLD and node.tag in ("strong", "b") and _stands_alone(node) else 0
        ) or (CARD if mode == CARD and node.tag != "-text" and _card_title(node) else 0)
        if rank:
            flush()
            text = _clean(node.text(separator=" "))
            if text:
                items.append((rank, text))
            headings.add(node.mem_id)
            continue
        if node.tag != "-text":
            continue
        parent, block = node.parent, None
        inside = False
        while parent is not None:
            if parent.mem_id in headings:
                inside = True
                break
            if block is None and parent.tag in BLOCKS:
                block = parent
            parent = parent.parent
        if inside:
            continue
        if block is not None and block.mem_id != block_id:
            flush()
            block_id = block.mem_id
        line.append(node.text_content or "")
    flush()
    return items


def _fragment(title: str) -> str:
    """A text fragment the browser scrolls to; dashes and commas are its own syntax."""
    return quote(title, safe="").replace("-", "%2D")


def read_headings(page: str, page_url: str, place: str | None = None) -> tuple[bool, list[RawJob]]:
    """Whether the page reads as a careers page, and a role for each role heading."""
    tree = HTMLParser(page)
    for node in tree.css(DROPPED):
        node.decompose()
    root = tree.css_first("main") or tree.body
    if root is None:
        return False, []
    careers = bool(CAREERS_WORDS.search(root.text(separator=" ")))

    items: list[tuple[int, str]] = []
    rank = 0
    # Real headings first, then bold lines standing alone, then card titles.
    for mode in (0, BOLD, CARD):
        items = _items(root, mode)
        ranks = Counter(r for r, text in items if r and _is_role(text))
        if ranks:
            # The rank most role headings share; the higher one when two tie.
            rank = max(ranks, key=lambda r: (ranks[r], -r))
            break
    if not rank:
        return careers, []

    jobs: dict[str, RawJob] = {}
    context: str | None = None
    index = 0
    while index < len(items):
        level, text = items[index]
        index += 1
        if not level or level > rank:
            continue
        if level < rank or not _is_role(text):
            if level < rank or level < BOLD:
                context = _place(text)
            continue

        lines: list[str] = []
        while index < len(items):
            level, line = items[index]
            if level and (level < rank or (level == rank and (level < BOLD or _is_role(line)))):
                break
            lines.append(line)
            index += 1

        where = _place(text)
        if where is None:
            details = [line for line in lines[:DETAIL_LINES] if len(line) <= DETAIL_LENGTH]
            where = next(filter(None, map(_place, details)), None)
        where = where or context or place

        key = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        if f"{page_url}#{key}" in jobs and where:
            key += "-" + re.sub(r"[^a-z0-9]+", "-", where.lower()).strip("-")
        base, count = key, 1
        while f"{page_url}#{key}" in jobs:
            count += 1
            key = f"{base}-{count}"
        job_id = f"{page_url}#{key}"
        jobs[job_id] = RawJob(
            source_job_id=job_id,
            title=text[:300],
            url=f"{page_url}#:~:text={_fragment(text)}",
            location_raw=where,
            description="".join(f"<p>{html.escape(line)}</p>" for line in lines) or None,
        )
    return True, list(jobs.values())


class VacancyHeadingsAdapter(BaseAdapter):
    name = "vacancy_headings"
    tier = 3

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        target, _, _, place = split_slug(slug)
        page_url = target if "//" in target else f"https://{target}"
        if is_excluded(page_url):
            raise ExcludedSite(f"{page_url} is on a site this project does not crawl")
        parts = urlsplit(page_url)
        if not RobotsPolicy(f"{parts.scheme}://{parts.netloc}", client).allows(page_url):
            raise PermissionError(f"robots.txt disallows {page_url}")

        response = client.get(page_url)
        response.raise_for_status()
        careers, jobs = read_headings(response.text, str(response.url).split("#")[0], place)
        if not careers:
            # A page that no longer reads as a careers page is a broken read: failing
            # keeps the last good list rather than closing every role.
            raise ValueError(f"{page_url} no longer reads as a careers page")
        return jobs


register(VacancyHeadingsAdapter())

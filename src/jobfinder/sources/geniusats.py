"""GeniusATS career sites.

    GET  https://{host}/search
    POST https://{host}/search/searchvacancies

A GeniusATS site (Genius Group's; Caffè Nero's is `careers.caffenero.com`) serves its
search page empty, then posts the page's own search form to `/search/searchvacancies`
as it loads. The answer is every open vacancy as HTML, one block per vacancy carrying
its id, title, place, pay, working pattern and contract type in hidden inputs, and a
count of them all. One post reads the board, and the read is checked against the count.

Places are shop names, "Dublin Merrion Row" beside "Putney Station NE", on a board that
covers the UK and Ireland. Pay in euro marks a role in the Republic, so those places are
read as Irish; the rest are left as the board names them.

The slug is the careers host.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from jobfinder.sources.base import BaseAdapter, PartialJobs, RawJob, register
from jobfinder.sources.jsonld import RobotsPolicy

SEARCH_FORM = "form[hx-post*=searchvacancies]"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def search_form(page: str) -> tuple[str, dict[str, str]] | None:
    """Where the search form posts and the fields it posts as the page loads."""
    form = HTMLParser(page).css_first(SEARCH_FORM)
    if form is None:
        return None
    fields: dict[str, str] = {}
    for node in form.css("input[name]"):
        if node.attributes.get("type") in ("checkbox", "radio") and "checked" not in node.attributes:
            continue
        fields[node.attributes["name"]] = node.attributes.get("value") or ""
    for node in form.css("select[name]"):
        option = node.css_first("option[selected]") or node.css_first("option")
        fields[node.attributes["name"]] = (option.attributes.get("value") if option else "") or ""
    return form.attributes.get("hx-post") or "/search/searchvacancies", fields


def parse_results(page: str, base: str) -> tuple[list[RawJob], int | None]:
    """Every vacancy in a search answer, and the count the answer gives for them."""
    tree = HTMLParser(page)
    jobs: dict[str, RawJob] = {}
    for block in tree.css("div.vacancy"):
        values = {
            node.attributes["name"]: _clean(node.attributes.get("value") or "")
            for node in block.css("input[type=hidden][name]")
        }
        job_id = values.get("vacancyId")
        link = block.css_first("a[href]")
        if not job_id or link is None:
            continue
        heading = block.css_first("h2")
        title = values.get("title") or (_clean(heading.text()) if heading is not None else "")
        if not title:
            continue
        place = values.get("locationName") or None
        salary = values.get("salary") or ""
        if place and "€" in salary:
            place = f"{place}, Ireland"
        hours = ", ".join(v for v in (values.get("workingPattern"), values.get("contractType")) if v)
        details = [f"{label}: {values[key]}" for label, key in
                   (("Pay", "salary"), ("Working pattern", "workingPattern"), ("Contract", "contractType"))
                   if values.get(key)]
        jobs.setdefault(job_id, RawJob(
            source_job_id=job_id,
            title=title[:300],
            url=urljoin(base + "/", link.attributes.get("href") or ""),
            location_raw=place,
            description=". ".join(details) or None,
            employment_type=hours or None,
        ))
    count = tree.css_first("#vacancy-count-display")
    stated = re.sub(r"\D", "", count.text()) if count is not None else ""
    return list(jobs.values()), int(stated) if stated else None


class GeniusAtsAdapter(BaseAdapter):
    name = "geniusats"
    tier = 1

    def _fetch(self, slug: str, client: httpx.Client) -> list[RawJob]:
        base = "https://" + slug.removeprefix("https://").strip("/")
        search_url = f"{base}/search"
        robots = RobotsPolicy(base, client)
        if not robots.allows(search_url):
            raise PermissionError(f"robots.txt disallows {search_url}")

        page = client.get(search_url)
        page.raise_for_status()
        form = search_form(page.text)
        if form is None:
            raise ValueError(f"no GeniusATS search form at {search_url}")
        action, fields = form
        answer = client.post(
            urljoin(base + "/", action), data=fields,
            headers={"HX-Request": "true", "Referer": search_url},
        )
        answer.raise_for_status()
        jobs, stated = parse_results(answer.text, base)
        if stated is None:
            # No count means the answer is not the list it was: a broken read, which
            # keeps the last good one rather than closing every role.
            raise ValueError(f"no vacancy count in the GeniusATS answer from {base}")
        return PartialJobs(jobs) if len(jobs) < stated else jobs


register(GeniusAtsAdapter())

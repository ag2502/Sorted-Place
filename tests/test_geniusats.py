"""GeniusATS career sites: the search form posted once, every vacancy in the answer."""

from __future__ import annotations

import httpx
import pytest
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.normalize.hours import is_part_time
from jobfinder.normalize.location import normalize_location
from jobfinder.registry.detect import detect_hosted_site
from jobfinder.sources.base import BaseAdapter
from jobfinder.sources.geniusats import GeniusAtsAdapter, parse_results

BASE = "https://careers.acme.ie"

# Shaped like careers.caffenero.com/search: the form htmx posts as the page loads.
SEARCH = """
<link href=https://cdn.ats.careers/gats-static/20260716-135727/sf-static/css/output.css rel=stylesheet>
<form id=searchForm hx-post=/search/searchvacancies hx-trigger="load, submit">
 <input name=Keywords value=""><input name=location value="">
 <input type=hidden name=IsRemote value=false>
 <select name=distance><option value=5>5 Miles</option><option value=12500 selected>100+ Miles</option></select>
 <select name=sortBy><option value=0>Distance</option></select>
</form>
"""


def _vacancy(job_id: int, title: str, place: str, salary: str, pattern: str) -> str:
    return (
        f'<div id=vacancy-{job_id} class="pb-3 group/vacancy vacancy">'
        f"<a href=/search/{job_id}/{title.lower().replace(' ', '-')} hx-boost=false>"
        f"<h2 class=theme-vacancy-list-jobtitle>{title}</h2><span class=font-bold>{place}</span>"
        f"<form id=fav-form hx-post=/search/vacancy/favvacancies>"
        f"<input type=hidden name=vacancyId value={job_id}>"
        f'<input type=hidden name=title value="{title}">'
        f'<input type=hidden name=locationName value="{place}">'
        f'<input type=hidden name=salary value="{salary}">'
        f'<input type=hidden name=workingPattern value="{pattern}">'
        f"<input type=hidden name=contractType value=Permanent></form></a></div>"
    )


RESULTS = (
    _vacancy(32681, "Shift Leader", "Dublin Merrion Row", "€14.35", "Full Time")
    + _vacancy(32470, "Barista", "Galway Eyre Square", "€13.50", "Part Time")
    + _vacancy(32757, "Shift Leader", "Putney Station NE", "to £12.91", "Full Time")
    + "<span id=vacancy-count-display hx-swap-oob=true> 3 </span>"
)


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(BaseAdapter, "polite_pause", staticmethod(lambda: None))


def test_each_vacancy_block_is_a_role_and_pay_in_euro_places_it_in_ireland():
    jobs, stated = parse_results(RESULTS, BASE)

    assert stated == 3
    assert [job.source_job_id for job in jobs] == ["32681", "32470", "32757"]
    dublin, galway, putney = jobs
    assert dublin.url == f"{BASE}/search/32681/shift-leader"
    assert dublin.location_raw == "Dublin Merrion Row, Ireland"
    assert normalize_location(galway.location_raw).is_ireland
    assert is_part_time(galway.title, galway.description, galway.employment_type)
    # A shop paid in sterling is left as the board names it.
    assert putney.location_raw == "Putney Station NE"
    assert not normalize_location(putney.location_raw).is_ireland


@respx.mock
def test_the_adapter_posts_the_search_form_and_reads_every_vacancy():
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(200, html=SEARCH))
    post = respx.post(f"{BASE}/search/searchvacancies").mock(
        return_value=httpx.Response(200, html=RESULTS)
    )

    result = GeniusAtsAdapter().fetch("careers.acme.ie")

    assert result.status is CrawlStatus.OK
    assert len(result.jobs) == 3
    sent = post.calls.last.request.content.decode()
    assert "distance=12500" in sent and "sortBy=0" in sent


@respx.mock
def test_an_answer_short_of_its_count_is_partial():
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(200, html=SEARCH))
    respx.post(f"{BASE}/search/searchvacancies").mock(
        return_value=httpx.Response(200, html=RESULTS.replace("> 3 <", "> 57 <"))
    )
    assert GeniusAtsAdapter().fetch("careers.acme.ie").status is CrawlStatus.PARTIAL


@respx.mock
def test_a_page_without_the_search_form_is_a_failed_read():
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/search").mock(return_value=httpx.Response(200, html="<p>Maintenance</p>"))
    assert GeniusAtsAdapter().fetch("careers.acme.ie").status is CrawlStatus.FAILED


def test_a_geniusats_site_is_recognised_on_the_employers_own_host():
    assert detect_hosted_site(f"{BASE}/search", SEARCH) == ("geniusats", "careers.acme.ie")

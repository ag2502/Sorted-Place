"""Smooth Careers lists on Shopify shops: every card a role, each role's page its advert."""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.normalize.hours import is_part_time
from jobfinder.normalize.location import normalize_location
from jobfinder.registry.detect import detect_hosted_site
from jobfinder.sources.base import BaseAdapter
from jobfinder.sources.smoothcareers import SmoothCareersAdapter, read_list

BASE = "https://www.acme.ie"
LISTING = f"{BASE}/pages/careers"


def _card(slug: str, title: str, place: str, hours: str, team: str, *details: str) -> str:
    # Shaped like DID Electrical's list on 17 Oct 2026.
    spans = "".join(
        f'<span class="detail"><span class="detail-text">{text}</span></span>'
        for text in (place, team.split(" http")[0], *details)
    )
    return (
        f'<a href="/pages/{slug}" target="_blank"><li class="smoothie-job-listing-card" '
        f'location="{place}" job-type="{hours}" team="{team}"><h3>{title}</h3>{spans}</li></a>'
    )


PAGE = (
    '<html><body><div id="smoothie-job-content"><ul id="smoothie-search-list">'
    + _card("appliance-installer-10", "Appliance Installer", "Fonthill", "FULL_TIME",
            "Logistics https://careers.smooth.ie/newapp/listing/edit/t._U1R?shop=acme.myshopify.com#",
            "3 Oct 2026", "Full time")
    + _card("temporary-sales-advisor-part-time-1", "PT - Temporary Sales Advisor (North Dublin)",
            "North Dublin", "PART_TIME", "Sales", "Part time")
    + _card("temporary-sales-advisor-16", "Temporary Sales Advisor", "Lucan", "FULL_TIME", "Sales",
            "Full time")
    + "</ul></div></body></html>"
)


def _role_page(advert: str, posted: str) -> str:
    return (
        '<script type="application/ld+json">{"@type":"JobPosting","title":"x",'
        f'"datePosted":"{posted}","employmentType":"PART_TIME"}}</script>'
        '<div id="smoothie-job-content" class="smoothie-listing-page">'
        f'<div class="smoothie-job-editor-content">{advert}</div></div>'
    )


# The shop's About page, where seven of DID's role pages had lost their advert.
ABOUT = "<html><body><h2>About Acme Electrical</h2><p>Since 1968.</p></body></html>"


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(BaseAdapter, "polite_pause", staticmethod(lambda: None))


def _mock_shop(roles: dict[str, httpx.Response]) -> None:
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(LISTING).mock(return_value=httpx.Response(200, html=PAGE))
    for slug, response in roles.items():
        respx.get(f"{BASE}/pages/{slug}").mock(return_value=response)


def test_every_card_is_a_role_with_its_store_hours_and_team():
    found, jobs = read_list(PAGE, LISTING)

    assert found
    installer, part_time, lucan = jobs
    assert installer.url == f"{BASE}/pages/appliance-installer-10"
    assert installer.location_raw == "Fonthill"
    assert normalize_location(installer.location_raw).is_dublin
    # The app's own edit link, left after the team's name, is dropped.
    assert installer.department == "Logistics"
    assert installer.employment_type == "Full time"
    assert installer.posted_at == datetime(2026, 10, 3)
    assert part_time.employment_type == "Part time"
    assert is_part_time(part_time.title, part_time.description, part_time.employment_type)
    assert lucan.posted_at is None


@respx.mock
def test_each_roles_page_adds_its_advert_and_one_that_lost_it_links_to_the_list():
    _mock_shop({
        "appliance-installer-10": httpx.Response(200, html=_role_page("<p>Install.</p>", "2026-09-28")),
        "temporary-sales-advisor-part-time-1": httpx.Response(
            200, html=_role_page("<p>Help customers.</p>", "2026-09-28")
        ),
        "temporary-sales-advisor-16": httpx.Response(200, html=ABOUT),
    })

    result = SmoothCareersAdapter().fetch("www.acme.ie/pages/careers")

    assert result.status is CrawlStatus.OK
    installer, part_time, lucan = result.jobs
    assert "<p>Install.</p>" in installer.description
    # The card's own date stands; the page's fills in where the card has none.
    assert installer.posted_at == datetime(2026, 10, 3)
    assert part_time.posted_at == datetime(2026, 9, 28)
    assert lucan.description is None
    assert lucan.url == LISTING
    assert lucan.source_job_id == f"{BASE}/pages/temporary-sales-advisor-16"


@respx.mock
def test_a_roles_page_that_cannot_be_read_keeps_its_own_link():
    _mock_shop({
        "appliance-installer-10": httpx.Response(503),
        "temporary-sales-advisor-part-time-1": httpx.Response(503),
        "temporary-sales-advisor-16": httpx.Response(503),
    })

    result = SmoothCareersAdapter().fetch("www.acme.ie/pages/careers")

    assert result.status is CrawlStatus.OK
    assert [job.url for job in result.jobs] == [job.source_job_id for job in result.jobs]


@respx.mock
def test_a_page_without_the_list_is_a_failed_read_and_an_empty_list_is_nothing_open():
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    page = respx.get(LISTING).mock(return_value=httpx.Response(200, html="<p>Maintenance</p>"))
    assert SmoothCareersAdapter().fetch("www.acme.ie/pages/careers").status is CrawlStatus.FAILED

    page.mock(return_value=httpx.Response(200, html='<ul id="smoothie-search-list"></ul>'))
    result = SmoothCareersAdapter().fetch("www.acme.ie/pages/careers")
    assert result.status is CrawlStatus.OK and result.jobs == []


def test_a_card_naming_no_store_takes_the_slugs_place():
    # Monroe's careers list on 20 Oct 2026: its cards leave the location blank.
    page = PAGE.replace('location="Lucan"', 'location=""')

    _, (installer, _, blank) = read_list(page, LISTING, "Galway, Ireland")

    assert installer.location_raw == "Fonthill"
    assert blank.location_raw == "Galway, Ireland"
    assert read_list(page, LISTING)[1][2].location_raw is None


def test_the_list_is_recognised_with_its_page_as_the_slug():
    assert detect_hosted_site(LISTING, PAGE) == ("smoothcareers", "www.acme.ie/pages/careers")
    # A role's own page is not the list.
    role = _role_page("<p>Install.</p>", "2026-09-28")
    assert detect_hosted_site(f"{BASE}/pages/appliance-installer-10", role) is None

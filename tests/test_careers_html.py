"""Reading vacancies straight from a careers page's HTML.

There is no markup to lean on here, so most of these tests are about what must *not* be
taken for a job: menus, advice pages, category links and news items all come in tidy
groups of sibling links, and every one of them was mistaken for a vacancy list by an
earlier version of the heuristics when run against the real blocked queue.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.sources.base import BaseAdapter
from jobfinder.sources.careers_html import (
    CareersHtmlAdapter,
    Link,
    job_candidates,
    page_links,
    parse_job_page,
)


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(BaseAdapter, "polite_pause", staticmethod(lambda: None))


def _listing(*items: tuple[str, str]) -> str:
    rows = "".join(f'<li><a href="{href}">{text}</a></li>' for href, text in items)
    return f"<html><body><nav><a href='/about'>About</a></nav><ul>{rows}</ul></body></html>"


def _advert(title: str, body: str = "Location: Dublin 2\nClosing date: 1 October") -> str:
    return (
        f"<html><body><header>Menu</header><main><h1>{title}</h1>"
        f"<p>{body}</p><a href='/apply'>Apply now</a></main></body></html>"
    )


def _mock_site(listing_html: str, adverts: dict[str, str]) -> None:
    respx.get("https://acme.ie/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/careers").mock(
        return_value=httpx.Response(200, html=listing_html)
    )
    for path, html in adverts.items():
        respx.get(f"https://acme.ie{path}").mock(return_value=httpx.Response(200, html=html))


# ---------------------------------------------------------------------------
# Recognising a vacancy list
# ---------------------------------------------------------------------------


def test_sibling_links_with_role_titles_are_a_vacancy_list():
    links = page_links(
        _listing(
            ("/vacancies/staff-nurse", "Staff Nurse"),
            ("/vacancies/senior-porter", "Senior Porter"),
            ("/vacancies/finance-officer", "Finance Officer"),
        ),
        "https://acme.ie/careers",
    )
    assert [link.text for link in job_candidates(links, "https://acme.ie/careers")] == [
        "Staff Nurse",
        "Senior Porter",
        "Finance Officer",
    ]


def test_a_card_linking_its_picture_first_is_judged_by_its_title():
    """Pat McDonnell Paints links each card's picture, then its title, then "Read More",
    all to the one advert. Judged by the picture's empty text, no card read as a role."""
    card = (
        '<div class="card"><a href="/careers/{slug}/"><img src="/{slug}.jpg"></a>'
        '<h4><a href="/careers/{slug}/">{title}</a></h4>'
        '<a href="/careers/{slug}/">Read More</a></div>'
    )
    html = "<html><body>" + "".join(
        card.format(slug=slug, title=title)
        for slug, title in (
            ("blanchardstown-sales-assistant-part-time", "SALES ASSISTANT - BLANCHARDSTOWN, DUBLIN 15"),
            ("limerick-warehouse-operative", "WAREHOUSE OPERATIVE - LIMERICK"),
            ("cork-sales-manager", "SALES MANAGER - CENTRE PARK ROAD, CORK"),
        )
    ) + "</body></html>"
    links = page_links(html, "https://acme.ie/careers/")
    assert [link.text for link in job_candidates(links, "https://acme.ie/careers/")] == [
        "SALES ASSISTANT - BLANCHARDSTOWN, DUBLIN 15",
        "WAREHOUSE OPERATIVE - LIMERICK",
        "SALES MANAGER - CENTRE PARK ROAD, CORK",
    ]


def test_a_card_with_a_heading_takes_the_heading_as_its_title():
    """John Paul's cards wrap the location, the title and the department in one link;
    the whole text read as "Dublin, County Dublin, Ireland Accounts Payable Assistant
    Finance"."""
    card = (
        '<a href="/jobs/{slug}/"><div class="location">Dublin, County Dublin, Ireland</div>'
        '<h3 class="title">{title}</h3><div class="dept">Finance</div></a>'
    )
    html = "<html><body>" + "".join(
        card.format(slug=slug, title=title)
        for slug, title in (("accounts-payable", "Accounts Payable Assistant"),
                            ("site-engineer", "Site Engineer"))
    ) + "</body></html>"
    links = page_links(html, "https://acme.ie/careers")
    assert [link.text for link in job_candidates(links, "https://acme.ie/careers")] == [
        "Accounts Payable Assistant",
        "Site Engineer",
    ]


def test_a_card_title_named_by_its_class_is_the_title_and_plurals_read_as_roles():
    """Cash & Carry Kitchens' cards hold a label, the list's name, the title and "Read
    more" in one link, with the title in a `card-generic__title` paragraph; most of its
    titles are plural ("Experienced Kitchen Fitters")."""
    card = (
        '<article><a href="/careers/{slug}" class="card-generic">'
        '<span class="card-generic__label">Careers</span>'
        '<p class="card-generic__subtitle">Showrooms</p>'
        '<p class="card-generic__title">Current Opportunities: {title}</p>'
        '<span class="button">Read more</span></a></article>'
    )
    html = "<html><body>" + "".join(
        card.format(slug=slug, title=title)
        for slug, title in (("kitchen-designers", "Kitchen Designer Positions"),
                            ("fitters-cork", "Experienced Kitchen Fitters - Cork"),
                            ("fitters-galway", "Experienced Kitchen Fitters - Galway"))
    ) + "</body></html>"
    links = page_links(html, "https://acme.ie/careers")
    assert [link.text for link in job_candidates(links, "https://acme.ie/careers")] == [
        "Kitchen Designer Positions",
        "Experienced Kitchen Fitters - Cork",
        "Experienced Kitchen Fitters - Galway",
    ]



def test_a_call_to_action_written_before_each_title_is_not_part_of_it():
    """Johnson and Perrott titles every vacancy article "Apply Now: Parts Advisor"."""
    card = (
        '<a href="/careers-apply-now/{slug}"><div class="article-info">'
        '<div class="article-title">Apply Now: {title}</div>'
        '<div class="article-date">14/10/2026</div></div></a>'
    )
    html = "<html><body>" + "".join(
        card.format(slug=slug, title=title)
        for slug, title in (("apply-now-parts-advisor", "Parts Advisor"),
                            ("apply-now-motor-technician", "Ford Motor Technician"))
    ) + "</body></html>"
    links = page_links(html, "https://acme.ie/careers-apply-now")
    assert [link.text for link in links] == ["Parts Advisor", "Ford Motor Technician"]

def test_a_careers_menu_is_not_a_vacancy_list():
    """Siblings under /careers/ with descriptive slugs - the shape of every brochure."""
    links = page_links(
        _listing(
            ("/careers/making-applications", "Making Applications"),
            ("/careers/mentoring-programme", "Mentoring Programme"),
            ("/careers/careers-information", "Careers Information & Resources"),
        ),
        "https://acme.ie/careers",
    )
    assert job_candidates(links, "https://acme.ie/careers") == []


def test_links_carrying_a_posting_id_count_even_with_generic_text():
    links = page_links(
        _listing(("/job?jobId=101", "Click here"), ("/job?jobId=102", "Click here")),
        "https://acme.ie/careers",
    )
    assert len(job_candidates(links, "https://acme.ie/careers")) == 2


def test_a_real_title_starting_with_a_navigation_word_is_kept():
    """`Graduate` is a menu item on its own but the start of many real roles."""
    links = page_links(
        _listing(
            ("/vacancies/1", "Graduate Engineer"),
            ("/vacancies/2", "Graduate Accountant"),
        ),
        "https://acme.ie/careers",
    )
    assert len(job_candidates(links, "https://acme.ie/careers")) == 2


def _shop_page(*roles: tuple[str, str], extra: str = "") -> str:
    """Shaped like DID Electrical's careers page on 17 Oct 2026: its adverts are Shopify
    pages, listed together among links to the shop's other pages."""
    shop = "".join(
        f'<div class="promo"><a href="/pages/{slug}">{text}</a></div>'
        for slug, text in (
            ("tv-installations", "TV Installations"), ("store-locator", "Find a Store"),
            ("delivery", "Delivery Information"), ("price-match", "Price Match Promise"),
            ("gift-cards", "Gift Cards"),
        )
    )
    cards = "".join(
        f'<a href="/pages/{slug}" target="_blank"><li class="smoothie-job-listing-card">'
        f"<h3>{title}</h3><span>North Dublin</span></li></a>"
        for slug, title in roles
    )
    return f"<html><body>{shop}<ul id='smoothie-search-list'>{cards}</ul>{extra}</body></html>"


DID_ROLES = (
    ("appliance-installer-10", "Appliance Installer"),
    ("temporary-sales-advisor-16", "Temporary Sales Advisor"),
    ("temporary-sales-part-time", "Cork - Temporary Sales (Part-Time)"),
)


def test_adverts_sharing_their_path_with_the_shops_pages_are_a_list_of_their_own():
    links = page_links(_shop_page(*DID_ROLES), "https://acme.ie/pages/careers")
    # As one group of `/pages/` siblings, the shop's pages outvote the adverts.
    assert job_candidates(links, "https://acme.ie/pages/careers") == []
    found = job_candidates(links, "https://acme.ie/pages/careers", by_container=True)
    # The list is judged whole, so an advert whose title names no role is kept with it.
    assert [link.text for link in found] == [title for _, title in DID_ROLES]


def test_a_list_of_shop_pages_is_not_a_vacancy_list_in_any_container():
    links = page_links(
        _listing(
            ("/pages/delivery", "Delivery Information"),
            ("/pages/price-match", "Price Match Promise"),
            ("/pages/gift-cards", "Gift Cards"),
        ),
        "https://acme.ie/pages/careers",
    )
    assert job_candidates(links, "https://acme.ie/pages/careers", by_container=True) == []


# ---------------------------------------------------------------------------
# Confirming a page is a job
# ---------------------------------------------------------------------------


def test_an_advert_is_read_with_its_labelled_location():
    link = Link("https://acme.ie/vacancies/1", "Staff Nurse", "Staff Nurse")
    job = parse_job_page(_advert("Staff Nurse"), link.url, link)
    assert job is not None
    assert job.title == "Staff Nurse"
    assert job.location_raw == "Dublin 2"


@pytest.mark.parametrize(
    "title", ["Full Price - IRL Sales Team Member", "Bar Staff", "Shift Leader", "Barista", "Cashier"]
)
def test_shop_floor_bar_and_kitchen_roles_are_roles(title: str):
    """Clarks' "Sales Team Member" adverts were dropped as naming no role, and every
    "Shift Leader" with them: `lead` stops short of "leader"."""
    link = Link("https://acme.ie/vacancies/1", title, title)
    assert parse_job_page(_advert(title), link.url, link) is not None


def test_a_membership_page_is_not_a_role():
    link = Link("https://acme.ie/join", "Become a Member", "")
    html = _advert("Become a Member", "Apply online and start training today.")
    assert parse_job_page(html, link.url, link) is None


def test_a_page_with_nothing_to_apply_for_is_not_a_job():
    """The Irish Times' "Jobs in Co. Antrim" pages are tidy siblings with no advert."""
    link = Link("https://acme.ie/jobs/co-antrim", "Staff Officer", "")
    html = "<html><body><main><h1>Jobs in Co. Antrim</h1><p>12 results</p></main></body></html>"
    assert parse_job_page(html, link.url, link) is None


def test_a_news_story_that_mentions_applying_is_not_a_job():
    link = Link("https://acme.ie/news/1", "Read more", "")
    html = _advert("Sustainability fund launched", "Community groups can apply from May.")
    assert parse_job_page(html, link.url, link) is None


def test_a_layout_word_after_a_location_label_is_not_a_place():
    link = Link("https://acme.ie/vacancies/1", "Chef", "Chef | Location | Apply")
    html = _advert("Chef", "Location\nApply\nBased at our Dublin 8 kitchen.")
    job = parse_job_page(html, link.url, link)
    assert job is not None
    assert job.location_raw == "Dublin 8, Ireland"


# ---------------------------------------------------------------------------
# The adapter end to end
# ---------------------------------------------------------------------------


@respx.mock
def test_the_list_one_click_past_a_brochure_is_found():
    respx.get("https://acme.ie/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ie/careers").mock(
        return_value=httpx.Response(
            200,
            html="<html><body><a href='/current-vacancies'>Current vacancies</a></body></html>",
        )
    )
    respx.get("https://acme.ie/current-vacancies").mock(
        return_value=httpx.Response(
            200,
            html=_listing(
                ("/vacancies/1", "Staff Nurse"), ("/vacancies/2", "Finance Officer")
            ),
        )
    )
    respx.get("https://acme.ie/vacancies/1").mock(
        return_value=httpx.Response(200, html=_advert("Staff Nurse"))
    )
    respx.get("https://acme.ie/vacancies/2").mock(
        return_value=httpx.Response(200, html=_advert("Finance Officer"))
    )

    result = CareersHtmlAdapter().fetch("https://acme.ie/careers")

    assert result.status is CrawlStatus.OK
    assert sorted(job.title for job in result.jobs) == ["Finance Officer", "Staff Nurse"]


@respx.mock
def test_a_role_naming_no_place_takes_the_slugs_place_after_its_title_and_advert():
    """Cash & Carry Kitchens hires in Ireland alone, and two of its seven adverts say only
    "Midlands" or nothing at all."""
    _mock_site(
        _listing(
            ("/vacancies/fitters-cork", "Kitchen Fitters - Cork"),
            ("/vacancies/fitters-midlands", "Kitchen Fitters - Midlands"),
            ("/vacancies/designer", "Kitchen Designer"),
        ),
        {
            "/vacancies/fitters-cork": _advert("Kitchen Fitters - Cork", "Apply with your CV."),
            "/vacancies/fitters-midlands": _advert("Kitchen Fitters - Midlands", "Apply with your CV."),
            "/vacancies/designer": _advert("Kitchen Designer", "Based in our Galway showroom."),
        },
    )

    placed = CareersHtmlAdapter().fetch("https://acme.ie/careers|place=Ireland")
    unplaced = CareersHtmlAdapter().fetch("https://acme.ie/careers")

    assert placed.status is CrawlStatus.OK
    assert {job.title: job.location_raw for job in placed.jobs} == {
        "Kitchen Fitters - Cork": "Cork, Ireland",
        "Kitchen Fitters - Midlands": "Ireland",
        "Kitchen Designer": "Galway, Ireland",
    }
    # Without a place the pipeline judges each role from its title and advert, as before.
    assert {job.location_raw for job in unplaced.jobs} == {None}


def _marked_up(title: str) -> str:
    return (
        '<script type="application/ld+json">{"@type":"JobPosting","title":"%s",'
        '"jobLocation":{"@type":"Place","address":"North Dublin"},'
        '"employmentType":"PART_TIME"}</script>' % title
    )


@respx.mock
def test_adverts_in_a_list_of_their_own_are_read_when_no_other_list_is_found():
    _mock_site(
        _shop_page(*DID_ROLES),
        {f"/pages/{slug}": _marked_up(title) for slug, title in DID_ROLES},
    )

    result = CareersHtmlAdapter().fetch("https://acme.ie/careers")

    assert result.status is CrawlStatus.OK
    assert sorted(job.title for job in result.jobs) == sorted(title for _, title in DID_ROLES)
    assert {job.location_raw for job in result.jobs} == {"North Dublin"}


@respx.mock
def test_the_list_one_click_away_wins_over_role_families_listed_on_the_brochure():
    """A container is only looked in once no page read has a list by its links' shape."""
    _mock_site(
        _shop_page(
            ("store-manager", "Store Manager"), ("sales-advisor", "Sales Advisor"),
            extra="<a href='/current-vacancies'>Current vacancies</a>",
        ),
        {
            "/current-vacancies": _listing(
                ("/vacancies/1", "Staff Nurse"), ("/vacancies/2", "Finance Officer")
            ),
            "/vacancies/1": _advert("Staff Nurse"),
            "/vacancies/2": _advert("Finance Officer"),
        },
    )

    result = CareersHtmlAdapter().fetch("https://acme.ie/careers")

    assert sorted(job.title for job in result.jobs) == ["Finance Officer", "Staff Nurse"]


@respx.mock
def test_a_paginated_list_is_reported_as_partial():
    """Only the first page is read, and the roles on the rest must not be closed."""
    html = _listing(
        ("/vacancies/1", "Staff Nurse"),
        ("/vacancies/2", "Finance Officer"),
    ).replace("</ul>", "</ul><a href='/careers?page=2'>Next</a>")
    _mock_site(
        html,
        {"/vacancies/1": _advert("Staff Nurse"), "/vacancies/2": _advert("Finance Officer")},
    )

    result = CareersHtmlAdapter().fetch("https://acme.ie/careers")

    assert result.status is CrawlStatus.PARTIAL
    assert len(result.jobs) == 2


@respx.mock
def test_a_site_that_disallows_crawling_is_not_read():
    respx.get("https://acme.ie/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )
    result = CareersHtmlAdapter().fetch("https://acme.ie/careers")
    assert result.status is CrawlStatus.FAILED
    assert "robots.txt" in (result.error or "")


def test_an_hr_page_about_staff_is_not_a_role():
    """UCC's "Staff Wellbeing" and "Staff Training" pages sat in its vacancy section."""
    link = Link("https://acme.ie/hr/wellbeing", "Staff Wellbeing", "")
    assert parse_job_page(_advert("Staff Wellbeing", "How to apply for support."), link.url, link) is None


def test_a_marked_up_advert_with_no_place_takes_dublin_from_its_title():
    link = Link("https://jobs.example.com/job/1", "", "")
    html = (
        '<script type="application/ld+json">{"@type":"JobPosting",'
        '"title":"Legal Advisor in Dublin","identifier":1}</script>'
    )
    job = parse_job_page(html, link.url, link)
    assert job is not None and job.location_raw == "Dublin, Ireland"

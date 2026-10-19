"""Jobtrain careers sites: a list of cards paged twelve at a time against its total."""

from __future__ import annotations

import httpx
import pytest
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.normalize.hours import is_part_time
from jobfinder.normalize.location import normalize_location
from jobfinder.registry.detect import detect_hosted_site
from jobfinder.sources.base import BaseAdapter
from jobfinder.sources.jobtrain import JobtrainAdapter

HOST = "careers.cmetb.ie"


def _card(job_id: int, title: str, place: str, contract: str, hours: str) -> str:
    # Shaped like Cavan and Monaghan ETB's cards on 19 Oct 2026.
    return f"""
    <div tabindex="-1" class="card mb-7 mb-md-20 job-card vacancies_no_logo btnJobMore">
      <div class="card-body p-20">
        <a tabindex="0" class="mb-15" href="/Job/JobDetail?JobId={job_id}">
          {title}
        </a>
        <div class="d-flex mb-15 job-card__location pl-7">
          <i class="material-icons-outlined">location_on</i><strong>
          {place}                </strong>
        </div>
        <p class="jobdetailsitem department"><strong>Department:</strong> {place}</p>
        <p class="jobdetailsitem employmenttype"><strong>Contract type:</strong> {contract}</p>
        <p class="jobdetailsitem hours"><strong>Hours per week:</strong> {hours} </p>
        <a class="job-card__link" href="/Job/JobDetail?JobId={job_id}">More..</a>
      </div>
    </div>"""


def _list(cards: list[str], total: int) -> str:
    return (f'<input type="hidden" id="totalCurrentRecords" value="{len(cards)}">'
            f'<input type="hidden" id="totalMatchRecords" value="{total}">' + "".join(cards))


CARETAKER = _card(615, "Temporary, Full-time Caretaker in Beech Hill College",
                  "Beech Hill College, Monaghan, Co. Monaghan", "Fixed-Term, Full-Time", "39")
DRIVER = _card(613, "Bus Driver - Part-time, Term-time Only", "Kingscourt Youthreach, Co. Cavan",
               "Fixed-Term, Part-Time", "20")
OFFICER = _card(609, "Information Officer", "CMETBs Further Education and Training (FET) Service",
                "Fixed-Term, Full-Time", "35")

ADVERT = """<html><head>
<script type="application/ld&#x2B;json">{"@type": "JobPosting", "datePosted": "2026-10-14"}</script>
</head><body><div id="Media_Rich_Advert"><p>Cavan and Monaghan ETB seeks a caretaker.</p></div></body></html>"""


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(BaseAdapter, "polite_pause", staticmethod(lambda: None))


def _mock(pages: list[str]) -> None:
    respx.get(f"https://{HOST}/robots.txt").mock(return_value=httpx.Response(404))
    for n, page in enumerate(pages):
        respx.get(f"https://{HOST}/Home/_JobCard", params={"Skip": str(n * 12)}).mock(
            return_value=httpx.Response(200, html=page))
    respx.get(url__startswith=f"https://{HOST}/Job/JobDetail").mock(return_value=httpx.Response(200, html=ADVERT))


@respx.mock
def test_each_card_is_a_role_with_its_contract_hours_and_advert():
    _mock([_list([CARETAKER, DRIVER, OFFICER], 3)])

    result = JobtrainAdapter().fetch(f"{HOST}|place=Cavan, Ireland")

    assert result.status is CrawlStatus.OK
    caretaker, driver, officer = result.jobs
    assert caretaker.source_job_id == "615"
    assert caretaker.url == f"https://{HOST}/Job/JobDetail?JobId=615"
    assert caretaker.location_raw == "Beech Hill College, Monaghan, Co. Monaghan"
    assert caretaker.employment_type == "Fixed-Term, Full-Time, 39 hours a week"
    assert "seeks a caretaker" in caretaker.description
    assert caretaker.posted_at.date().isoformat() == "2026-10-14"
    assert is_part_time(driver.title, "", driver.employment_type)
    # A card naming nowhere in Ireland is given the slug's place.
    assert officer.location_raw == "CMETBs Further Education and Training (FET) Service, Cavan, Ireland"
    assert normalize_location(officer.location_raw).is_ireland


@respx.mock
def test_the_list_is_paged_until_every_role_it_counts_is_read():
    _mock([_list([CARETAKER, DRIVER], 3), _list([OFFICER], 3)])

    result = JobtrainAdapter().fetch(HOST)

    assert [job.source_job_id for job in result.jobs] == ["615", "613", "609"]


@respx.mock
def test_a_short_read_or_a_page_with_no_list_fails_rather_than_closing_roles():
    _mock([_list([CARETAKER], 3), _list([], 3)])
    assert JobtrainAdapter().fetch(HOST).status is CrawlStatus.FAILED

    respx.get(f"https://{HOST}/Home/_JobCard").mock(return_value=httpx.Response(200, html="<p>Not found</p>"))
    assert JobtrainAdapter().fetch(HOST).status is CrawlStatus.FAILED


def test_a_jobtrain_site_is_detected_by_its_assets():
    page = '<link rel="icon" href="/images/Jobtrain_favicon.ico?v=z9k"><div data-request-url="/Home/_JobCard">'
    assert detect_hosted_site("https://careers.cmetb.ie/Home/Job", page) == ("jobtrain", "careers.cmetb.ie")

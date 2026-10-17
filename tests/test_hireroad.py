"""HireRoad careers pages: a public JSON list, paged against its total."""

from __future__ import annotations

import httpx
import pytest
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.normalize.hours import is_part_time
from jobfinder.normalize.location import normalize_location
from jobfinder.registry.detect import detect_in_text
from jobfinder.sources.base import BaseAdapter
from jobfinder.sources.hireroad import HireRoadAdapter

API = "https://api.hireroadrecruit.com/api/public/careers/regatta-ireland"


def _job(job_id: str, title: str, place: str, pay: float, period: str = "hourly", **extra) -> dict:
    # Shaped like Regatta Ireland's list on 17 Oct 2026.
    return {
        "id": job_id, "title": title, "location": place, "employmentType": "permanent",
        "salaryMin": pay, "salaryCurrency": "EUR", "salaryPeriod": period,
        "publishedAt": "2026-10-16T11:26:26.278Z", "closingDate": "2026-11-13T18:30:00.000Z",
        **extra,
    }


JOBS = [
    _job("pMvmHSXNp7LKIJjbXz-3Q", "Sales Assistant - 8 Hours Per Week", "Santry, D9", 14.15),
    _job("nud0rXfxgWA-KmWXY6gG7", "Assistant Manager - 37.5 Hours Per Week", "Clondalkin, D22", 20000, "annual"),
    _job("fH61414df-4dyWe2WO_Id", "Sales Assistant", "Clonmel, Co Tipperary", 14.15,
         jobTime="part_time", hoursPerWeek=10),
]


def _page(jobs: list[dict], total: int, page: int = 1, pages: int = 1) -> dict:
    return {"success": True, "data": {"jobs": jobs, "total": total, "page": page,
                                      "limit": 100, "totalPages": pages}}


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(BaseAdapter, "polite_pause", staticmethod(lambda: None))


@respx.mock
def test_every_role_is_read_with_its_advert_pay_and_hours():
    respx.get(f"{API}/jobs").mock(return_value=httpx.Response(200, json=_page(JOBS, 3)))
    respx.get(url__regex=rf"{API}/jobs/[\w-]+").mock(return_value=httpx.Response(
        200, json={"success": True, "data": {"description": "<p>Join our <strong>Omni</strong> store.</p>"}}
    ))

    result = HireRoadAdapter().fetch("regatta-ireland")

    assert result.status is CrawlStatus.OK
    santry, clondalkin, clonmel = result.jobs
    assert santry.url == "https://app.hireroadrecruit.com/careers/regatta-ireland/jobs/pMvmHSXNp7LKIJjbXz-3Q"
    assert normalize_location(santry.location_raw).is_dublin
    assert santry.description.startswith("<p>Join our")
    assert "<p>Pay: €14.15 an hour</p>" in santry.description
    assert santry.employment_type == "Permanent"
    assert is_part_time(santry.title, santry.description, santry.employment_type)
    assert "€20,000 a year" in clondalkin.description
    assert not is_part_time(clondalkin.title, clondalkin.description, clondalkin.employment_type)
    # A working-hours label, where the customer sets one, is passed on.
    assert clonmel.employment_type == "Part time, Permanent"
    assert "Hours: 10 hours a week" in clonmel.description
    assert is_part_time(clonmel.title, clonmel.description, clonmel.employment_type)


@respx.mock
def test_the_list_is_read_page_by_page():
    pages = respx.get(f"{API}/jobs").mock(side_effect=[
        httpx.Response(200, json=_page(JOBS[:2], 3, page=1, pages=2)),
        httpx.Response(200, json=_page(JOBS[2:], 3, page=2, pages=2)),
    ])
    respx.get(url__regex=rf"{API}/jobs/[\w-]+").mock(return_value=httpx.Response(404))

    result = HireRoadAdapter().fetch("regatta-ireland")

    assert result.status is CrawlStatus.OK
    assert len(result.jobs) == 3
    assert pages.calls[1].request.url.params["page"] == "2"
    # A role whose advert could not be read keeps its pay.
    assert result.jobs[0].description == "<p>Pay: €14.15 an hour</p><p>Contract: Permanent</p>"


@respx.mock
def test_a_list_short_of_its_total_is_partial():
    respx.get(f"{API}/jobs").mock(return_value=httpx.Response(200, json=_page(JOBS, 31)))
    respx.get(url__regex=rf"{API}/jobs/[\w-]+").mock(return_value=httpx.Response(404))
    assert HireRoadAdapter().fetch("regatta-ireland").status is CrawlStatus.PARTIAL


@respx.mock
def test_an_answer_without_a_job_list_is_a_failed_read():
    respx.get(f"{API}/jobs").mock(return_value=httpx.Response(
        200, json={"success": False, "error": {"code": "NOT_FOUND"}}
    ))
    assert HireRoadAdapter().fetch("regatta-ireland").status is CrawlStatus.FAILED


def test_a_framed_hireroad_page_is_recognised():
    html = '<iframe src="https://app.hireroadrecruit.com/careers/regatta/group"></iframe>'
    assert detect_in_text(html) == ("hireroad", "regatta")

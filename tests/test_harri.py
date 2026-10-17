"""Harri careers pages: a group read whole, its Irish roles kept by address."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.normalize.location import normalize_location
from jobfinder.sources.base import BaseAdapter
from jobfinder.sources.harri import RECORD, SEARCH, HarriAdapter

PROFILE = "https://gateway.harri.com/core/api/v1/profile/slug/hawksmoorgroup"


def _role(job_id: int, title: str, brand: str, address: str, code: str, pay=None) -> dict:
    # Shaped like the gateway's search answer on 17 Oct 2026.
    return {
        "id": job_id, "aliasPosition": title, "position": {"name": title},
        "brand": {"id": job_id * 10, "name": brand, "slug": brand.replace(" ", "-")},
        "locations": [{"city": "x", "country_code": code, "formatted_address": address}],
        "publishTime": "2026-10-16T14:11:35Z", "compensation": pay,
    }


HOURLY = {"code": "RANGE", "rate": {"code": "PER_HOUR"}, "compensation_from": 21.33, "compensation_to": 21.65}

ROLES = [
    _role(2791739, "Waiter", "Hawksmoor Dublin", "34 College Green, Dublin, D02 C850, Ireland", "IE", HOURLY),
    # Coded UK beside an Eircode: the address decides.
    _role(2833001, "Sales Advisor (8hrs)", "Crew Clothing Dundrum",
          "Unit 7, Level 2\tDundrum Town Centre, Dundrum, D16 X4K7, United Kingdom", "UK"),
    # Coded IE, but Belfast is not in the Republic.
    _role(2797181, "General Manager - Belfast", "KFC Ireland & Northern Ireland", "Belfast, BT1, Ireland", "IE"),
    _role(2842700, "Bar Manager", "Hawksmoor - Boston", "15 Necco Street, Boston, Massachusetts 02210, United States", "US"),
]


def _answer(results: list[dict], hits: int) -> httpx.Response:
    return httpx.Response(200, json={"data": {"hits": hits, "results": results}, "status": "SUCCESS"})


def _record(description: str = "<p>Deliver warm service.</p>", **job) -> httpx.Response:
    return httpx.Response(200, json={"data": {"job": {"description": description, **job}}})


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(BaseAdapter, "polite_pause", staticmethod(lambda: None))


@respx.mock
def test_only_the_roles_placed_in_the_republic_are_kept():
    respx.get(PROFILE).mock(return_value=httpx.Response(200, json={"data": {"id": 1274345, "type": "brand"}}))
    search = respx.post(SEARCH).mock(return_value=_answer(ROLES, 4))
    respx.get(RECORD.format(job_id=2791739)).mock(return_value=_record())
    respx.get(RECORD.format(job_id=2833001)).mock(return_value=_record(
        "<p>Join our Dundrum team.</p>", employment_types=["PART_TIME"], weekly_work_time=8,
    ))

    result = HarriAdapter().fetch("hawksmoorgroup")

    assert result.status is CrawlStatus.OK
    assert [job.source_job_id for job in result.jobs] == ["2791739", "2833001"]
    waiter, advisor = result.jobs
    assert json.loads(search.calls.last.request.content)["brand_level_ids"] == [1274345]
    assert waiter.url == "https://harri.com/Hawksmoor-Dublin/job/2791739-waiter"
    assert waiter.department == "Hawksmoor Dublin"
    assert waiter.description == "<p>Deliver warm service.</p><p>Pay: €21.33 to €21.65 an hour</p>"
    assert advisor.location_raw == "Unit 7, Level 2 Dundrum Town Centre, Dundrum, D16 X4K7, United Kingdom"
    assert normalize_location(advisor.location_raw).is_dublin
    assert advisor.employment_type == "Part time"
    assert "<p>Hours: 8 hours a week</p>" in advisor.description


@respx.mock
def test_a_group_is_read_two_hundred_at_a_time_and_a_numeric_slug_needs_no_lookup():
    search = respx.post(SEARCH).mock(side_effect=[_answer(ROLES[:2], 4), _answer(ROLES[2:], 4)])
    respx.get(url__regex=r".*/profile/job/\d+").mock(return_value=_record())

    result = HarriAdapter().fetch("6152722")

    assert result.status is CrawlStatus.OK
    assert len(result.jobs) == 2
    bodies = [json.loads(call.request.content) for call in search.calls]
    assert [body["start"] for body in bodies] == [0, 2]
    assert bodies[0]["brand_level_ids"] == [6152722]


@respx.mock
def test_an_answer_short_of_its_total_is_partial():
    respx.post(SEARCH).mock(side_effect=[_answer(ROLES, 900), _answer([], 900)])
    respx.get(url__regex=r".*/profile/job/\d+").mock(return_value=_record())
    assert HarriAdapter().fetch("6152722").status is CrawlStatus.PARTIAL


@respx.mock
@pytest.mark.parametrize("answer", [
    httpx.Response(400, json={"data": "Slug not found", "status": "Error"}),
    httpx.Response(200, json={"data": {"id": 17259106, "type": "brand", "is_archived": True}}),
])
def test_a_page_gone_or_archived_is_a_failed_read(answer):
    respx.get(PROFILE).mock(return_value=answer)
    assert HarriAdapter().fetch("hawksmoorgroup").status is CrawlStatus.FAILED

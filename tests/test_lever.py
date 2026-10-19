"""Lever boards, in its US region and its EU one."""

from __future__ import annotations

import httpx
import respx

from jobfinder.registry.detect import detect_in_text
from jobfinder.sources.lever import LeverAdapter

POSTING = [{
    "id": "4f1c",
    "text": "Sales Associate",
    "hostedUrl": "https://jobs.eu.lever.co/karllagerfeld/4f1c",
    "categories": {"location": "Kildare Village, Kildare", "commitment": "Part-time", "team": "Retail"},
    "createdAt": 1760000000000,
}]


@respx.mock
def test_a_board_in_levers_eu_region_is_read_from_its_own_host():
    route = respx.get("https://api.eu.lever.co/v0/postings/karllagerfeld").mock(
        return_value=httpx.Response(200, json=POSTING))

    (job,) = LeverAdapter().fetch("eu:karllagerfeld").jobs

    assert route.called
    assert job.title == "Sales Associate"
    assert job.location_raw == "Kildare Village, Kildare"
    assert job.employment_type == "Part-time"


@respx.mock
def test_a_us_board_is_read_as_before():
    route = respx.get("https://api.lever.co/v0/postings/acme").mock(return_value=httpx.Response(200, json=POSTING))
    assert LeverAdapter().fetch("acme").jobs[0].source_job_id == "4f1c"
    assert route.called


def test_an_eu_board_is_detected_with_its_region():
    assert detect_in_text('<a href="https://jobs.eu.lever.co/karllagerfeld">Jobs</a>') == ("lever", "eu:karllagerfeld")
    assert detect_in_text('<a href="https://jobs.lever.co/acme">Jobs</a>') == ("lever", "acme")

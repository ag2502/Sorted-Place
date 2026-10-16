"""PostingPanda store job boards: one list for the island, the Republic's roles kept."""

from __future__ import annotations

import httpx
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.sources.postingpanda import PostingPandaAdapter, parse_list

HOST = "storejobs.example.co.uk"


def _card(job_id: str, title: str, place: str, contract: str = "Permanent") -> str:
    return f"""
<div class="col-span-12 md:col-span-4 bg-white rounded-2xl">
  <h3 class="h4 text-blue titleheight"><a href="jobdetails?jobPost={job_id}">{title}
    <style>.titleheight{{min-height:80px;}}</style></a></h3>
  <div class="mt-3 space-y-2 text-gray-600 locationheight">
    <div class="flex items-start gap-2"><img src="location.svg" alt="Location">
      <p class="text-sm">{place} <style>.locationheight{{min-height:230px;}}</style></p></div>
    <div class="flex items-start gap-2"><img src="clock.svg" alt="clock"><p class="text-sm">{contract}</p></div>
    <div class="flex items-start gap-2"><img src="salary.svg" alt="salary"><p class="text-sm">€14.15 to €14.85</p></div>
  </div>
  <div class="mt-auto pt-4"><a href="jobdetails?jobPost={job_id}">VIEW JOB</a></div>
</div>"""


LIST = f"""<html><body><h3 class="text-lg">Showing 3 results</h3>
{_card("945095", "In Store Team Member", "Athlone, County Westmeath, Ireland")}
{_card("951230", "Delivery Driver", "Coalisland, County Tyrone, Northern Ireland")}
{_card("971348", "Shift Runner, Clonmel", "Clonmel, County Tipperary, Ireland")}
</body></html>"""

ADVERT = """<html><head><script type="application/ld+json">{"@context": "https://schema.org/",
"@type": "JobPosting", "title": "In Store Team Member", "description": "<p>Flexible shifts.</p>",
"datePosted": "2026-09-05", "employmentType": ["PART_TIME"],
"identifier": {"@type": "PropertyValue", "name": "Shorecal", "value": "SM1674448AthISTM"}}</script></head></html>"""


def test_cards_are_read_once_each_without_their_style_rules():
    cards, total = parse_list(LIST)
    assert total == 3
    assert [(c["id"], c["title"], c["place"], c["contract"]) for c in cards] == [
        ("945095", "In Store Team Member", "Athlone, County Westmeath, Ireland", "Permanent"),
        ("951230", "Delivery Driver", "Coalisland, County Tyrone, Northern Ireland", "Permanent"),
        ("971348", "Shift Runner, Clonmel", "Clonmel, County Tipperary, Ireland", "Permanent"),
    ]


@respx.mock
def test_only_the_republics_roles_are_kept_with_the_hours_from_their_advert(monkeypatch):
    monkeypatch.setattr(PostingPandaAdapter, "polite_pause", staticmethod(lambda: None))
    listing = respx.get(f"https://{HOST}/AllJobs").mock(return_value=httpx.Response(200, text=LIST))
    respx.get(f"https://{HOST}/jobdetails", params={"jobPost": "945095"}).mock(
        return_value=httpx.Response(200, text=ADVERT))
    respx.get(f"https://{HOST}/jobdetails", params={"jobPost": "971348"}).mock(
        return_value=httpx.Response(500))

    result = PostingPandaAdapter().fetch(f"{HOST}|Ireland")

    assert result.status is CrawlStatus.OK
    assert [(j.source_job_id, j.title) for j in result.jobs] == [
        ("945095", "In Store Team Member"), ("971348", "Shift Runner, Clonmel"),
    ]
    team_member, shift_runner = result.jobs
    assert team_member.employment_type == "PART_TIME"
    assert team_member.description == "<p>Flexible shifts.</p>"
    assert team_member.url == f"https://{HOST}/jobdetails?jobPost=945095"
    # An advert that would not load leaves the card's own details.
    assert (shift_runner.employment_type, shift_runner.description) == ("Permanent", None)
    assert listing.calls[0].request.url.params["LocationCountry"] == "Ireland"


@respx.mock
def test_a_list_read_short_of_its_stated_size_is_partial(monkeypatch):
    monkeypatch.setattr(PostingPandaAdapter, "polite_pause", staticmethod(lambda: None))
    short = LIST.replace("Showing 3 results", "Showing 40 results")
    respx.get(f"https://{HOST}/AllJobs").mock(side_effect=[
        httpx.Response(200, text=short), httpx.Response(200, text="<p>Showing 40 results</p>"),
    ])
    respx.get(f"https://{HOST}/jobdetails").mock(return_value=httpx.Response(404))

    result = PostingPandaAdapter().fetch(HOST)

    assert result.status is CrawlStatus.PARTIAL
    assert len(result.jobs) == 2

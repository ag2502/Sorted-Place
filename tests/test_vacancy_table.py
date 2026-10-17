"""Reading vacancies listed as the rows of a table on a careers page."""

from __future__ import annotations

from datetime import datetime

import httpx
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.normalize.hours import is_part_time
from jobfinder.normalize.location import normalize_location
from jobfinder.sources.vacancy_table import VacancyTableAdapter, read_tables

# Shaped like Homesavers' careers page: a row per store's opening and one shared form.
STORES = """
<table class="hs-careers__table">
 <thead><tr>
  <th scope="col">S. No.</th><th scope="col">County</th><th scope="col">Role</th>
  <th scope="col">Job Location</th><th scope="col">Job ID</th><th scope="col">Type of Job</th>
  <th scope="col">Job Posted Date</th><th scope="col"><span>Apply</span></th>
 </tr></thead>
 <tbody>
  <tr><td>1</td><td>Athlone</td><td>Sales Assistant</td><td>Homesavers Diskin Centre</td>
   <td>09-509</td><td>Part Time</td><td>07-10-2026</td>
   <td><a href="https://acme.ie/apply/?role=Sales%20Assistant&#038;loc=Homesavers%20Diskin%20Centre&#038;ref=09-509">Apply Now</a></td></tr>
  <tr><td>2</td><td>Longford</td><td>Store Manager</td><td>Homesavers Axis Centre Longford</td>
   <td>09-520</td><td>Full Time / Part Time</td><td>01-10-2026</td>
   <td><a href="/apply/?role=Store%20Manager&#038;ref=09-520">Apply Now</a></td></tr>
  <tr><td>3</td><td>Athlone</td><td>Team Leader</td><td>Athlone</td>
   <td>09-508</td><td>Part Time</td><td>07-10-2026</td><td></td></tr>
 </tbody>
</table>
"""

OPENING_HOURS = """
<table><tr><th>Day</th><th>Opens</th><th>Closes</th></tr>
<tr><td>Monday</td><td>9:00</td><td>18:00</td></tr></table>
"""


def test_each_row_of_a_vacancy_table_is_a_role():
    found, jobs = read_tables(STORES, "https://acme.ie/careers/")

    assert found
    assert [job.source_job_id for job in jobs] == ["09-509", "09-520", "09-508"]
    first, manager, leader = jobs
    assert first.title == "Sales Assistant"
    assert first.location_raw == "Homesavers Diskin Centre, Athlone"
    assert first.url.endswith("loc=Homesavers%20Diskin%20Centre&ref=09-509")
    assert first.posted_at == datetime(2026, 10, 7)
    assert is_part_time(first.title, first.description, first.employment_type)
    # A store named after its town keeps the town as a part of its own, which places it.
    assert manager.location_raw == "Homesavers Axis Centre Longford, Longford"
    assert manager.url == "https://acme.ie/apply/?role=Store%20Manager&ref=09-520"
    assert normalize_location(manager.location_raw, company_is_irish=True).region == "Longford"
    # A row with no link of its own points at the page, and the town is not repeated.
    assert leader.url == "https://acme.ie/careers/"
    assert leader.location_raw == "Athlone"


def test_a_table_that_names_no_roles_is_not_vacancies():
    assert read_tables(OPENING_HOURS, "https://acme.ie/visit/") == (False, [])


@respx.mock
def test_the_adapter_reads_the_table_and_fails_when_it_is_gone():
    respx.get("https://acme.ie/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get("https://acme.ie/careers/")
    route.mock(return_value=httpx.Response(200, html=f"<html><body>{STORES}</body></html>"))

    result = VacancyTableAdapter().fetch("https://acme.ie/careers/")
    assert result.status is CrawlStatus.OK
    assert len(result.jobs) == 3

    # A redesign that drops the table is a broken read, never "nothing open".
    route.mock(return_value=httpx.Response(200, html=f"<html><body>{OPENING_HOURS}</body></html>"))
    assert VacancyTableAdapter().fetch("https://acme.ie/careers/").status is CrawlStatus.FAILED


@respx.mock
def test_an_empty_vacancy_table_is_nothing_open():
    respx.get("https://acme.ie/robots.txt").mock(return_value=httpx.Response(404))
    empty = STORES.split("<tbody>")[0] + "<tbody></tbody></table>"
    respx.get("https://acme.ie/careers/").mock(return_value=httpx.Response(200, html=empty))

    result = VacancyTableAdapter().fetch("https://acme.ie/careers/")
    assert result.status is not CrawlStatus.FAILED
    assert result.jobs == []


@respx.mock
def test_a_table_on_a_page_robots_txt_closes_is_not_read():
    respx.get("https://acme.ie/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /careers/\n")
    )
    result = VacancyTableAdapter().fetch("https://acme.ie/careers/")
    assert result.status is CrawlStatus.FAILED
    assert "robots.txt" in (result.error or "")

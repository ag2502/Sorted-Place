"""Reading vacancies written out on a careers page, each under its own heading."""

from __future__ import annotations

import httpx
import respx

from jobfinder.core.models import CrawlStatus
from jobfinder.normalize.hours import is_part_time
from jobfinder.normalize.location import normalize_location
from jobfinder.normalize.text import html_to_text
from jobfinder.sources.vacancy_headings import VacancyHeadingsAdapter, read_headings

PAGE = "https://acme.ie/careers/"

# Shaped like Budget's careers page: an <h2> per role, its place in the title and an
# email address to apply.
DEPOTS = """
<html><body><main>
 <h1>Careers</h1>
 <h2>Manager – Drumcondra, Dublin</h2>
 <p>We are looking for an experienced and self-motivated manager.</p>
 <p>Please forward CV and a cover letter to careers@acme.ie</p>
 <h2>Sales Rental Agent – Galway City</h2>
 <p>Customer service at the rental desk.</p>
 <h2>Rental Sales Agent – Belfast Airports</h2>
 <p>Rental desk at both airports.</p>
</main></body></html>
"""

# Shaped like Soundstore's: an <h1> per role and an <h3> per part of its advert, one of
# which reads as a role itself.
SHOPS = """
<html><body>
 <h1>Retail Support (Part Time) Midleton</h1>
 <h3>About Us:</h3><p>A leading electrical retailer in Munster.</p>
 <h3>HOURS:</h3><p>10-20 hours per week</p>
 <h1>Retail Sales Consultant - Join the team in Waterford</h1>
 <h3>Position: Retail Sales Consultants</h3><p>Help customers choose.</p>
 <p>Applications by email to jobs@acme.ie</p>
</body></html>
"""

# Shaped like Horans' and McGuirks': each shop's town heads its roles, a region heading
# names no town, and the same role is open in two shops.
TOWNS = """
<html><body>
 <h2>Current Openings</h2>
 <h3>Castlebar</h3>
 <h4>Nutritional Wellness Retail Staff</h4><p>Full-time and part-time roles.</p>
 <h3>Manor West Tralee:</h3>
 <h4>Nutritional Wellness Retail Staff</h4><p>To apply, send your C.V. to jobs@acme.ie</p>
 <h3>Online</h3>
 <h4>Ecommerce Assistant</h4><p>Pack and post orders.</p>
 <div id="accessibly-config" style="display:none">{"themeColor":"#81a749","showLogo":true}</div>
</body></html>
"""

# Shaped like Modern Tyres': no heading for its role, only bold lines, and the whole page
# inside a form.
BOLD = """
<html><body><form action="/careers" method="post">
 <h1>Careers</h1>
 <p>A family run business with genuine opportunities.</p>
 <p><strong>Modern Tyres Tallaght - Tyre Fitter</strong></p>
 <p>We are looking for an experienced tyre fitter.</p>
 <p><strong>The successful candidate will:</strong></p>
 <ul><li>Have experience in fitting car tyres</li></ul>
 <p><strong>Reward</strong></p>
 <p>A competitive salary. Please apply below with your current CV.</p>
 <input type="email" name="email" value="Your email">
 <h3>Documents</h3>
 <p>Application form</p>
</form></body></html>
"""

# Shaped like Mocha Beans': the place on the first short line under each role.
CAFES = """
<html><body>
 <h1>We're hiring</h1>
 <p>Bakery</p>
 <h3>Baker</h3><p>Full-time · Galway · Immediate start</p><p>Sourdough experience required</p>
 <p>Cafés</p>
 <h3>Barista</h3><p>Full-time &amp; part-time · Galway</p>
 <h2>Upload your CV</h2><p>Your CV is only used for recruitment.</p>
</body></html>
"""

NONE_OPEN = """
<html><body><h1>Careers</h1>
<p>We have no vacancies at the moment. Send your CV to jobs@acme.ie</p></body></html>
"""


def _mock(html: str):
    respx.get("https://acme.ie/robots.txt").mock(return_value=httpx.Response(404))
    return respx.get(PAGE).mock(return_value=httpx.Response(200, html=html))


# Kelly's Resort Hotel: each role a card, its title a paragraph classed "title".
CARDS = """<html><body><h1>Job Opportunities at Kelly's</h1>
<p>Come and join our team. Email your CV to the address below.</p>
<div class="card"><p class="title">Waiter/Waitress</p><p class="subtitle">Full Time</p>
<p class="description">Proven experience in a similar role.</p>
<a class="btn btn-primary" href="https://acme.ie/celebrate/">More Info</a></div>
<div class="card"><p class="title">Accommodation Assistant</p><p class="subtitle">Full Time/Part Time</p>
<p class="description">Keep our rooms spotless.</p>
<a class="btn btn-primary" href="https://acme.ie/celebrate/">More Info</a></div>
<div class="footer"><p class="subtitle">Follow us</p></div>
</body></html>"""

def test_each_role_heading_is_a_role_with_the_text_under_it():
    careers, jobs = read_headings(DEPOTS, PAGE)

    assert careers
    manager, galway, belfast = jobs
    assert manager.title == "Manager – Drumcondra, Dublin"
    assert manager.source_job_id == f"{PAGE}#manager-drumcondra-dublin"
    assert manager.url == f"{PAGE}#:~:text=Manager%20%E2%80%93%20Drumcondra%2C%20Dublin"
    assert manager.location_raw == "Drumcondra, Dublin"
    assert manager.description == (
        "<p>We are looking for an experienced and self-motivated manager.</p>"
        "<p>Please forward CV and a cover letter to careers@acme.ie</p>"
    )
    assert galway.location_raw == "Galway City"
    assert normalize_location(galway.location_raw, company_is_irish=True).region == "Galway"
    # A place in the North is kept as named, so the role never reads as the Republic's.
    assert belfast.location_raw == "Belfast Airports"
    assert not normalize_location(belfast.location_raw, company_is_irish=True).is_ireland


def test_the_roles_rank_is_the_one_most_role_headings_share():
    _, jobs = read_headings(SHOPS, PAGE)

    support, sales = jobs
    assert support.location_raw == "Midleton"
    assert "<p>HOURS:</p><p>10-20 hours per week</p>" in support.description
    assert is_part_time(support.title, html_to_text(support.description), support.employment_type)
    # A phrase naming the town gives its county, and a part heading stays in its role.
    assert sales.location_raw == "Waterford, Ireland"
    assert "<p>Position: Retail Sales Consultants</p>" in sales.description
    assert sales.url.endswith("#:~:text=Retail%20Sales%20Consultant%20%2D%20Join%20the%20team%20in%20Waterford")


def test_a_town_heading_places_the_roles_under_it_and_each_heading_starts_afresh():
    _, jobs = read_headings(TOWNS, PAGE, place="Ireland")

    castlebar, tralee, online = jobs
    assert castlebar.location_raw == "Castlebar"
    assert tralee.location_raw == "Manor West Tralee"
    # The same role in a second shop is told apart by its place.
    assert castlebar.source_job_id == f"{PAGE}#nutritional-wellness-retail-staff"
    assert tralee.source_job_id == f"{PAGE}#nutritional-wellness-retail-staff-manor-west-tralee"
    # "Online" names no place, so Tralee is not inherited and the slug's place stands.
    assert online.location_raw == "Ireland"
    # A widget's settings in a hidden div are not part of the last advert.
    assert online.description == "<p>Pack and post orders.</p>"


def test_bold_lines_are_read_when_no_heading_names_a_role():
    careers, jobs = read_headings(BOLD, PAGE)

    assert careers
    (fitter,) = jobs
    assert fitter.title == "Modern Tyres Tallaght - Tyre Fitter"
    assert normalize_location(fitter.location_raw).is_dublin
    # Bold lines that are not roles are parts of the advert, which ends at the next
    # real heading.
    assert "<p>The successful candidate will:</p>" in fitter.description
    assert "<p>Reward</p>" in fitter.description
    assert "Application form" not in fitter.description


def test_card_titles_are_read_when_neither_headings_nor_bold_lines_name_a_role():
    careers, jobs = read_headings(CARDS, PAGE)

    assert careers
    waiter, rooms = jobs
    assert waiter.title == "Waiter/Waitress"
    assert rooms.title == "Accommodation Assistant"
    assert "Full Time/Part Time" in rooms.description
    assert is_part_time(rooms.title, html_to_text(rooms.description), rooms.employment_type)
    assert not is_part_time(waiter.title, html_to_text(waiter.description), waiter.employment_type)


def test_a_heading_over_a_group_of_roles_is_not_a_role():
    page = """<html><body><h1>Careers</h1><p>Join our team.</p>
    <h3>Chef/Kitchen/Café Vacancies</h3>
    <h3>Café Supervisor</h3><p>Location: Arboretum Kilquade, Wicklow</p>
    <h3>Deli Assistant (Full-Time)</h3><p>Location: Leighlinbridge, Carlow</p>
    </body></html>"""

    _, jobs = read_headings(page, PAGE)

    assert [job.title for job in jobs] == ["Café Supervisor", "Deli Assistant (Full-Time)"]

def test_the_first_short_line_under_a_role_places_it():
    _, jobs = read_headings(CAFES, PAGE)

    baker, barista = jobs
    assert baker.location_raw == "Galway"
    assert barista.location_raw == "Galway"
    # Read as the pipeline reads it, from the advert's text.
    assert is_part_time(barista.title, html_to_text(barista.description), barista.employment_type)
    # The advert ends at a heading above the roles' rank.
    assert "recruitment" not in barista.description


@respx.mock
def test_the_slugs_place_is_given_only_to_roles_placed_nowhere():
    _mock(DEPOTS.replace("Sales Rental Agent – Galway City", "Sales Rental Agent"))

    result = VacancyHeadingsAdapter().fetch(f"{PAGE}|place=Ireland")

    assert result.status is CrawlStatus.OK
    manager, unplaced, belfast = result.jobs
    assert manager.location_raw == "Drumcondra, Dublin"
    assert unplaced.location_raw == "Ireland"
    assert belfast.location_raw == "Belfast Airports"


@respx.mock
def test_a_careers_page_with_nothing_open_is_empty_and_any_other_page_is_a_failed_read():
    route = _mock(NONE_OPEN)
    result = VacancyHeadingsAdapter().fetch(PAGE)
    assert result.status is CrawlStatus.OK
    assert result.jobs == []

    # A page that no longer reads as the careers page would close every role.
    route.mock(return_value=httpx.Response(200, html="<html><body><h1>Page not found</h1></body></html>"))
    assert VacancyHeadingsAdapter().fetch(PAGE).status is CrawlStatus.FAILED


@respx.mock
def test_a_page_robots_txt_closes_is_not_read():
    respx.get("https://acme.ie/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /careers/\n")
    )
    result = VacancyHeadingsAdapter().fetch(PAGE)
    assert result.status is CrawlStatus.FAILED
    assert "robots.txt" in (result.error or "")


def test_an_accordion_headings_title_is_read_through_its_button():
    # Shaped like Edvard & Pink's careers page on 19 Oct 2026.
    page = """<html><body><h2>Careers at Edvard &amp; Pink</h2><h3>Our Vacancies</h3>
    <h3 class="wp-block-woocommerce-accordion-header"><button class="accordion-item__toggle">
    <span>Massage Therapist</span><span class="accordion-item__toggle-icon"><svg></svg></span></button></h3>
    <div><p>Apply now by emailing careers@edvardandpink.ie</p></div></body></html>"""

    _, jobs = read_headings(page, PAGE)

    assert [job.title for job in jobs] == ["Massage Therapist"]


def test_a_hosts_heading_reads_as_a_role():
    # Jump Zone's: an <h2> for the role, an <h3> for each of its parts.
    page = """<html><body><h1>Join the team at Jump Zone!</h1>
    <h2>Court Monitor / Party Host</h2><h3>Court Monitor</h3><p>Monitor the trampoline areas.</p>
    <h3>Party Host</h3><p>Host parties held at the park.</p>
    <h3>Benefits</h3><p>Extremely flexible working. To apply, upload your CV.</p></body></html>"""

    _, jobs = read_headings(page, PAGE)

    (role,) = jobs
    assert role.title == "Court Monitor / Party Host"
    assert "Host parties held at the park." in role.description


def test_a_bold_first_line_broken_from_its_paragraph_is_a_role():
    # Tote Ireland's recruitment page on 19 Oct 2026.
    page = """<html><body><h2>Recruitment</h2>
    <p><strong>Part Time Racecourse Work With Tote - Recruiting Now</strong><br><br>Tote Ireland are
    recruiting part time staff to sell bets at racecourses around Ireland.<br><br>To apply, please
    send your CV to <strong><a href="/email">jobs@tote.ie</a></strong>.</p></body></html>"""

    _, jobs = read_headings(page, PAGE, "Ireland")

    (role,) = jobs
    assert role.title == "Part Time Racecourse Work With Tote - Recruiting Now"
    assert "sell bets at racecourses" in role.description
    assert role.location_raw == "Ireland"
    # A bold lead-in that runs on into its sentence is no heading.
    _, jobs = read_headings(page.replace("</strong><br><br>", "</strong> "), PAGE, "Ireland")
    assert jobs == []

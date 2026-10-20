"""Part-time classification. Every case is taken from a live Irish advert."""

from __future__ import annotations

import pytest

from jobfinder.normalize.hours import is_part_time


@pytest.mark.parametrize(
    "employment_type",
    ["Part time", "Part-time", "PART_TIME", "PartTime", "parttime_permanent",
     "FULL_TIME, PART_TIME", "parttime, perm", "Casual"],
)
def test_board_label_says_part_time(employment_type: str) -> None:
    assert is_part_time("Customer Assistant", None, employment_type)


@pytest.mark.parametrize(
    "employment_type", ["Full time", "FULL_TIME", "fulltime_permanent", "Permanent", None, ""],
)
def test_board_label_says_full_time(employment_type: str | None) -> None:
    assert not is_part_time("Customer Assistant", None, employment_type)


@pytest.mark.parametrize(
    "title",
    [
        "Part Time Retail Sales Consultant - Bray",
        "Room Attendant (Part Time)",
        "Commis Chef Full Time/Part Time",
        "PART TIME WEEKEND RETAIL POSITION",
        "Executive Officer (0.5FTE)",
        "Research Assistant, 0.3FTE, Specified Purpose",
        "Customer Service Advisor 25 hours per week",
        "Casual Event Staff",
        "Weekend Staff - Galway",
        "Hourly Paid Assistant Lecturer (Panel) in Mechanical Engineering",
    ],
)
def test_title_says_part_time(title: str) -> None:
    assert is_part_time(title)


@pytest.mark.parametrize(
    "title",
    [
        "Production Operator - Permanent Contract- Weekend Evening shift",
        "AI Support Engineer - Dublin (Weekend Shift)",
        "Software Engineer",
        "Store Manager",
    ],
)
def test_title_does_not_say_part_time(title: str) -> None:
    assert not is_part_time(title)


@pytest.mark.parametrize(
    "description",
    [
        "Job type: Permanent / Part-Time Hours: 15 hrs/wk Reports To: CEO",
        "Hours of Work: 25.5 hours monthly Contract Type: Perm, part-time",
        "Circle K, Athy is now hiring for a Part-time, Deli Assistant. Work on a 15-hour "
        "weekly contract (2 days per week).",
        "This is a part-time, permanent opportunity.",
        "Cleaning Operative to join our team in Letterkenny on a part time basis.",
        "Permanent Full Time and Part Time Contracts Available",
        "We are now recruiting Social Care Worker for full and part time work",
        "These roles offer a flexible number of hours, with an option for part-time or "
        "full-time work.",
        "Clinical Nurse Specialist (Urology) Permanent 24 Hours per Week",
        "Contract/Duration Up to 36 months (0.6 FTE)",
        "temporary part-time role, offering 11.5–14 hours per week",
    ],
)
def test_advert_says_part_time(description: str) -> None:
    assert is_part_time("Customer Assistant", description)


@pytest.mark.parametrize(
    "description",
    [
        # The candidate's past, not this role.
        "Helpful if you have: Experience gained through an internship, part-time role, "
        "volunteering, university society.",
        "Relevant internship, part-time work, university projects or extracurriculars.",
        "Experience in quantity surveying as a part-time position in either practice or "
        "contracting would be beneficial.",
        # A colleague, a pay clause, a course.
        "The team comprises a Managing Pharmacist, another part-time Technician role and "
        "2 Sales Assistants.",
        "Salary scale €47,273 - €60,250, pro rata for shorter and/or part-time contracts.",
        "plus potential support for part-time training (relevant to the position)",
        "a graduate on the part time course studying Construction Economics",
        "is offering one part-time funded scholarships for entry into the PhD programme",
        "This may vary from site ownership to part time support of others",
        # Full-time hours.
        "Hours: 39 hours per week, Monday to Friday.",
        "As part of Ireland's largest private hospital group",
    ],
)
def test_advert_does_not_say_part_time(description: str) -> None:
    assert not is_part_time("Graduate Quantity Surveyor", description)


def test_weekly_hours_far_down_the_advert_are_ignored() -> None:
    description = "Full time role in our Dublin office. " + "Duties include. " * 120 + (
        "Staff may take up to 20 hours per week of study leave in exam season."
    )
    assert not is_part_time("Accountant", description)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Retail Sales Advisor - 20 Hours", True),
        ("Retail Assistant (Athlete) - PT 20H - Kildare", True),
        ("Retail Assistant (Temp Athlete) - PT8HRS Kildare", True),
        ("8hr tailor experience essential", True),
        ("30hr Keyholder", False),
        ("Store Manager (39 hours)", False),
        ("Staff Nurse - 12 Hour Shifts", False),
        ("Staff Nurse - 12 hours shifts", False),
        ("24 Hour Gym Receptionist", False),
    ],
)
def test_a_weekly_contract_stated_in_the_title(title: str, expected: bool) -> None:
    """DFS, Nike and Levi's write the contract bare in the title."""
    assert is_part_time(title) is expected


@pytest.mark.parametrize(
    "title",
    [
        "Relief Social Care Worker, Valentia Residential Service",
        "Relief Early Years Educator - Dublin (Multiple Centres)",
        "Health Care Assistant - Relief",
        "Social Care Worker (Full time & Relief Both)",
        "Staff Bank Healthcare Assistant, HSE South West STAFFBANK1",
        "Staff Chiropodist / Podiatrist, Bank",
        "Dsesb, Staff Nurse, Bank Older Persons Services",
        "Christmas Bank Care Workers",
    ],
)
def test_relief_and_bank_roles_are_shifts_as_needed(title: str) -> None:
    assert is_part_time(title, None, "Full time")


@pytest.mark.parametrize(
    "title",
    [
        "Relief Pharmacist - Finglas Area",
        "Relief Shop Manager",
        "Relief Van Driver",
        "Relief Utilities Operator",
        "Senior Process Safety Engineer - Pressure Relief Specialist",
        "Hydro Team Member - General Operative and Bank Ranger",
        "2027 Commercial & Investment Bank - Global Payments Analyst Program",
        "Accounts Assistant (Accounts Payable and Bank Reconciliations)",
    ],
)
def test_full_time_cover_and_other_banks_are_not(title: str) -> None:
    assert not is_part_time(title, None, "Full time")


@pytest.mark.parametrize(
    "description",
    [
        "Midweek availability is essential.\n24-32 hours per week.",
        "Candidates must be flexible to work 18–30 hours per week.",
        "This is permanent contract offering 20 - 40 hours per week depending on you.",
    ],
)
def test_a_range_of_hours_offering_a_part_time_week(description: str) -> None:
    assert is_part_time("Sales Assistant", description, None)
    assert is_part_time("Banqueting Operations Assistant - 24/32 hours weekly")


def test_a_full_time_range_of_hours_is_not() -> None:
    assert not is_part_time("Support Worker", "Hours: 35-39 hours per week.", None)


def test_a_full_or_part_time_field_name_leaves_the_answer_to_its_value() -> None:
    """ISM's recruitment adverts carry a "Full/Part Time:" field on every role."""
    advert = "Location: Dublin 13\nVacancy Type: Permanent\nFull/Part Time: {}\n\nSalary: €16 p/h"
    assert not is_part_time("Bendi Forklift Driver", advert.format("Full-Time"), None)
    assert is_part_time("Warehouse Operative", advert.format("Part-Time"), None)
    assert is_part_time("Sales Assistant", "Full or part time hours available.", None)

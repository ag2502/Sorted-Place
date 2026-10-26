"""Standing calls for CVs are not jobs; roles that mention talent or interest are."""

from __future__ import annotations

import pytest

from jobfinder.normalize.vacancy import is_not_a_vacancy


@pytest.mark.parametrize("title", [
    "Register Your Interest",
    "Graduate Electrical Engineer – Register Your Interest",
    "Register Your Interest: Future Opportunities with Uisce Éireann",
    "Join Our Talent Community",
    "Housekeeper - Talent Pool",
    "Join AECOM's Quantity Surveyor Talent Network",
    "Future Opportunities – Join Our Talent Pipeline",
    "Expression of Interest - Power Transmission & Distribution Professionals (Design) - Dublin",
    "Expressions of Interest: Clinical Lead, BowelScreen Programme",
    "Express Your Interest",
    "General Application",
    "Speculative Applications (Quantum Computing)",
    "Speculative Energy Candidates",
    "Open Application (m/f/d)",
    "EMEA: General Interest Submission",
    "Future Opportunity: Sales Development Representative",
    "Future Sales Openings: Account Executive, US |Remote|",
    "Personal Banking Associate - ( Future Opportunities )",
    "Submit Your CV",
    "Can't find a role for you? Submit a general application.",
    "Don't see a role that interests you but want to keep in touch?  Upload your details here",
    "Not the right position? Send us your application anyway!",
    "Interested in Working with Everseen?",
])
def test_a_standing_call_for_cvs_is_not_a_vacancy(title):
    assert is_not_a_vacancy(title)


@pytest.mark.parametrize("title", [
    "Talent Acquisition Partner",
    "Talent Pool Coordinator",
    "Voice Talent - German - Remote",
    "Consultant Paediatrician with a special interest in Neurodisability",
    "Application Support Analyst",
    "C&D Foods | Graduate Applications",
    "Open Cheesemonger Application",
    "2027 Future Leaders Academy: Limerick Tax & Legal - Business Tax Graduate Opportunity",
    "IT Systems Manager, UCD IT Services (with 6 month panel for future permanent and temporary roles)",
    "Part Time Pool Lifeguard",
    "Software Engineer 3, Data Pipeline",
    "",
    None,
])
def test_a_real_role_is_kept(title):
    assert not is_not_a_vacancy(title)

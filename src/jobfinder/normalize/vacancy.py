"""Is an advert a job at all?

Many boards keep a standing "Register Your Interest" or "Join Our Talent Community"
posting beside their real roles, for collecting CVs against openings that do not exist
yet. They look like jobs to a crawler, but there is nothing to apply for: the employer
files the CV and may never write back. Across the live Irish adverts about 250 are this
kind, a few with the same title at dozens of shops.

Only the title is read. A real role sometimes says "submit your CV" in its advert, and a
talent pool's advert reads like any other, so the description would be no help either
way. The phrases are the ones the boards actually use; a role that only mentions talent
("Talent Acquisition Partner", "Talent Pool Coordinator") is left alone.
"""

from __future__ import annotations

import re

NOT_A_VACANCY = re.compile(
    r"""
    \bregister(?:\s+your)?\s+interest\b
    | \bexpress(?:ion|ions)?\s+(?:your\s+|of\s+)?interest\b
    | \btalent\s+(?:pool|community|network|pipeline|bank)s?\b
        (?!\s+(?:manager|coordinator|co-ordinator|lead|partner|specialist|advisor|officer|executive))
    | \b(?:general|open|unsolicited)\s+(?:application|applicant|candidate|interest)s?\b
    | \bspeculative\b
    | \bfuture\s+(?:\w+\s+)?(?:opportunit(?:y|ies)|vacanc(?:y|ies)|roles|positions|openings)\b
    | \b(?:submit|send|upload|leave)\s+(?:us\s+)?your\s+(?:cv|resume|details|application)\b
    | \b(?:can['’]?t|don['’]?t|didn['’]?t)\s+(?:find|see)\s+(?:a|the|your)\s+(?:right\s+)?(?:role|job|position)\b
    | \bnot\s+the\s+right\s+(?:role|job|position)\b
    | ^\s*interested\s+in\s+(?:working|joining)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def is_not_a_vacancy(title: str | None) -> bool:
    """True for a standing call for CVs rather than a role someone can be hired into."""
    return bool(title and NOT_A_VACANCY.search(title))

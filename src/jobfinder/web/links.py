"""Which addresses may become a link.

A URL reaches a page from an employer's careers site, an event listing, or a form post,
and escaping it does not stop `javascript:` or `data:` running when it is clicked. So
every href printed from data goes through `safe_link`, in the site's templates (as the
`link` filter) and in the emails.
"""

from __future__ import annotations

import re

# A web address, or a path on this site. The prefix is checked on the raw string, so a
# scheme hidden behind a tab or a control character, which browsers strip, fails it. A
# path may not start with `//` or `/\`, which browsers read as another site.
_LINKABLE = re.compile(r"^(?:https?://|/(?![/\\]))", re.IGNORECASE)


def safe_link(value) -> str:
    """`value` when it is safe to put in an href, otherwise "#"."""
    url = str(value or "").strip()
    return url if _LINKABLE.match(url) else "#"

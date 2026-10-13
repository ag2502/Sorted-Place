"""Only web addresses and the site's own paths may become links."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jobfinder.web.app import templates
from jobfinder.web.links import safe_link

SRC = Path(__file__).resolve().parents[1] / "src" / "jobfinder"


@pytest.mark.parametrize("url", [
    "https://jobs.example.com/apply?id=1",
    "http://example.ie/careers",
    "HTTPS://EXAMPLE.COM",
    "/events/12",
    "/profile#alerts",
])
def test_web_addresses_and_own_paths_are_kept(url):
    assert safe_link(url) == url


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    " javascript:alert(1)",
    "java\tscript:alert(1)",
    "\x01javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "//evil.example",
    "/\\evil.example",
    "jobs.example.com",
    "",
    None,
])
def test_anything_else_becomes_a_dead_link(url):
    assert safe_link(url) == "#"


def test_an_apply_button_never_carries_a_script():
    html = templates.get_template("_apply_button.html").render(
        url="javascript:alert(document.cookie)", advert_key="k", title="t", company="c",
        account=None, accounts_enabled=False,
    )
    assert "javascript:" not in html


# An href printed from a value, as opposed to a string written into the template.
_PRINTED_HREF = re.compile(r'href="\{\{\s*([^}]*?)\s*\}\}')


@pytest.mark.parametrize("folder", ["web/templates", "alerts/templates"])
def test_every_printed_href_goes_through_the_link_filter(folder):
    unfiltered = []
    for path in sorted((SRC / folder).glob("*.html")):
        for expression in _PRINTED_HREF.findall(path.read_text()):
            literal = expression.startswith(("'", '"'))
            own_site = expression.startswith(("site", "manage", "unsubscribe", "stop("))
            if "|link" not in expression and not literal and not own_site:
                unfiltered.append(f"{path.name}: {expression}")
    assert not unfiltered, "add |link to these hrefs: " + ", ".join(unfiltered)

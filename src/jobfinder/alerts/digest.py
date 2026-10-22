"""Build and send each subscriber's digest of newly opened jobs.

Runs after every crawl (crawl.yml), and sends to a subscriber only when three things
hold: an alert is on, it is due, and something new has opened that it covers. An email
with nothing in it is never sent.

Every digest goes out at one time of day, 19:00 in Ireland, and covers what opened since
the one before: the last 24 hours for a daily alert, the last week for a weekly one.

What counts as new is deliberately strict. A job must have been first seen after the
subscriber's last email, and, where the employer gives a posting date, posted within a
week of it. The second rule is what stops a newly crawled employer, whose adverts are
all "first seen" today, from arriving as a flood of months-old roles.

The matching is the site's own search (`web.app._search_results`), so an alert never
lists a job that the same search on the site would not. A followed company is the
exception: its section is every new job it has in Ireland, as its window on /companies
lists them, internships and all.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from zoneinfo import ZoneInfo

from jinja2 import Environment, PackageLoader, select_autoescape

from jobfinder.alerts import service
from jobfinder.core.config import settings
from jobfinder.web.links import safe_link

logger = logging.getLogger(__name__)

DUBLIN = ZoneInfo("Europe/Dublin")
# The evening send, Irish time, summer or winter. Nothing sends before it; the first
# crawl after it does. The crawl is dispatched on the hour (crawl.yml), so mail lands a
# little after seven, and a crawl that is late sends late rather than skipping a day.
SEND_AT = time(19, 0)
# A weekly alert waits for the seventh evening after its last email.
WEEKLY_GAP = timedelta(days=6)
# An advert whose own date is this much older than the last email is not new, however
# recently we first read it.
POSTED_GRACE = timedelta(days=7)
# Jobs listed in one section of an email; the rest are one click away.
SHOWN = 12
MAX_EMAILS = 400

_env = Environment(
    loader=PackageLoader("jobfinder.alerts", "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True, lstrip_blocks=True,
)
_env.filters["link"] = safe_link


@dataclass
class Section:
    key: str
    heading: str
    noun: str            # "internship", "graduate programme", "part-time job", "job"
    items: list[dict]
    more_url: str
    at: str = ""         # the company, for a followed one: "3 new jobs at Stripe"

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass
class Summary:
    subscribers: int = 0
    due: int = 0
    sent: int = 0
    empty: int = 0
    failed: int = 0
    notes: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        text = (f"alerts: {self.subscribers} subscribers, {self.due} due, {self.sent} sent, "
                f"{self.empty} with nothing new, {self.failed} failed")
        return text + ("; " + "; ".join(self.notes) if self.notes else "")


def _time(value) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def send_slot(now: datetime) -> datetime:
    """Today's send time in Ireland, in UTC."""
    day = now.astimezone(DUBLIN).date()
    return datetime.combine(day, SEND_AT, DUBLIN).astimezone(timezone.utc)


def is_due(row: dict, now: datetime) -> bool:
    """Past this evening's send time, and not yet sent this evening (or this week)."""
    slot = send_slot(now)
    if now < slot:
        return False
    last = _time(row.get("last_sent_at"))
    gap = WEEKLY_GAP if row.get("frequency") == "weekly" else timedelta(0)
    return last is None or last < slot - gap


def since_for(row: dict) -> datetime:
    """New means new since the last email, or since the alert was switched on."""
    return _time(row.get("last_sent_at")) or _time(row.get("created_at")) or (
        datetime.now(timezone.utc) - timedelta(days=1)
    )


def _is_new(item: dict, since: datetime) -> bool:
    from jobfinder.web.app import _as_utc

    job = item["job"]
    first_seen, posted = _as_utc(job.first_seen_at), _as_utc(job.posted_at)
    if not first_seen or first_seen <= since:
        return False
    return posted is None or posted >= since - POSTED_GRACE


def _section(key: str, heading: str, noun: str, profile: dict, since: datetime) -> Section:
    from jobfinder.web.app import _search_results, _search_url

    # Every field the search reaches, as before newest first could keep to the picked
    # ones, and the link asks for the same so it shows what the email listed.
    context = _search_results(profile, per_page=100_000, since=since, scope="all")
    items = [i for i in context["items"] if _is_new(i, since)]
    link = settings.site_url.rstrip("/") + _search_url(
        {**profile, "sort": "newest"}, {"view": "list", "scope": "all"}
    )
    return Section(key, heading, noun, items, link)


def _company_sections(names: list[str], since: datetime) -> list[Section]:
    """One section per followed company with something new, in the order followed."""
    from jobfinder.web.app import (
        Company, JobPosting, _experience_label, _facts_for, _in_ireland, _is_offerable,
        _place_label, company_links, session_scope,
    )
    from sqlalchemy import select

    if not names:
        return []
    by_name: dict[str, list[dict]] = {name: [] for name in names}
    with session_scope() as session:
        rows = session.execute(
            select(JobPosting, Company.name)
            .join(Company, Company.id == JobPosting.company_id)
            .where(Company.name.in_(names), _is_offerable(), _in_ireland(),
                   JobPosting.first_seen_at > since.replace(tzinfo=None))
            .order_by(JobPosting.first_seen_at.desc())
        ).all()
        for job, name in rows:
            stated = _facts_for(job)
            by_name[name].append({
                "job": job, "company": name, "salary": stated["salary"],
                "mode": stated["mode"], "experience": _experience_label(job),
                "place": _place_label(job),
            })
    site = settings.site_url.rstrip("/")
    links = company_links(names)
    return [
        Section(f"company:{name}", f"New at {name}", "job",
                [i for i in items if _is_new(i, since)], site + links[name], at=name)
        for name, items in by_name.items()
    ]


def sections_for(row: dict, profile: dict | None, since: datetime) -> list[Section]:
    """What this subscriber asked for, each with the jobs new since `since`.

    A job appearing in two sections (a graduate programme in the searcher's own field,
    or a followed company's job in it) is listed once, in the first.
    """
    profile = profile or {}
    fields = [f for f in (profile.get("fields") or [])]
    out: list[Section] = []
    if row.get("internships"):
        out.append(_section("internships", "New internships", "internship",
                            {"fields": fields, "internships_only": True}, since))
    if row.get("graduate"):
        out.append(_section("graduate", "New graduate programmes", "graduate programme",
                            {"fields": fields, "graduate_only": True}, since))
    if row.get("part_time"):
        # Like the site's own switch, part-time is a whole search: every part-time job
        # in Ireland, the saved fields only putting their roles first.
        out.append(_section("part_time", "New part-time jobs", "part-time job",
                            {"fields": fields, "part_time_only": True}, since))
    if row.get("jobs") and fields:
        out.append(_section("jobs", "New jobs in your fields", "job", {
            "fields": fields, "years": profile.get("years"),
            "include_remote": bool(profile.get("include_remote")),
        }, since))
    out.extend(_company_sections(list(row.get("companies") or []), since))
    seen: set[int] = set()
    for section in out:
        section.items = [i for i in section.items if i["job"].id not in seen]
        seen.update(i["job"].id for i in section.items)
    return [s for s in out if s.items]


def _plural(n: int, noun: str, at: str = "") -> str:
    where = f" at {at}" if at else ""
    if n == 1:
        return f"1 new {noun}{where}"
    return f"{n} new " + ("graduate programmes" if noun == "graduate programme" else noun + "s") + where


def subject(sections: list[Section]) -> str:
    parts = [_plural(s.count, s.noun) for s in sections if not s.at]
    followed = [s for s in sections if s.at]
    if len(followed) == 1:
        parts.append(_plural(followed[0].count, "job", followed[0].at))
    elif followed:
        # Named one by one, five followed companies would run past what a subject shows.
        parts.append(_plural(sum(s.count for s in followed), "job", "companies you follow"))
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return joined[0].upper() + joined[1:] + " in Ireland"


def compose(row: dict, sections: list[Section]) -> EmailMessage:
    site = settings.site_url.rstrip("/")
    unsubscribe = f"{site}/alerts/unsubscribe?token={row['token']}"
    context = {
        "sections": sections, "shown": SHOWN, "site": site,
        "manage": f"{site}/profile#alerts", "unsubscribe": unsubscribe,
        "frequency": row.get("frequency") or "daily",
    }
    message = EmailMessage()
    message["Subject"] = subject(sections)
    name, address = parseaddr(settings.smtp_from or settings.smtp_user)
    message["From"] = formataddr((name or "Sorted Place", address))
    message["To"] = row["email"]
    # One-click unsubscribe (RFC 8058): mail apps show their own button, which posts here.
    message["List-Unsubscribe"] = f"<{unsubscribe}>"
    message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content(_env.get_template("digest.txt").render(**context))
    message.add_alternative(_env.get_template("digest.html").render(**context), subtype="html")
    return message


def _smtp() -> smtplib.SMTP:
    if settings.smtp_port == 465:
        server = smtplib.SMTP_SSL(settings.smtp_host, 465, context=ssl.create_default_context(),
                                  timeout=30)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        server.starttls(context=ssl.create_default_context())
    if settings.smtp_user:
        server.login(settings.smtp_user, settings.smtp_password)
    return server


def run(*, dry_run: bool = False, now: datetime | None = None) -> Summary:
    """Send every digest that is due. Never raises for one subscriber's failure."""
    summary = Summary()
    if not service.configured():
        summary.notes.append("no service role key: nothing read (set SUPABASE_SERVICE_ROLE_KEY)")
        return summary
    sending = bool(settings.smtp_host) and not dry_run
    if not settings.smtp_host and not dry_run:
        summary.notes.append("no SMTP host set: reporting only, nothing sent")
    now = now or datetime.now(timezone.utc)
    if now < send_slot(now):
        summary.notes.append(f"before {SEND_AT:%H:%M} in Ireland: nothing is due until then")
        return summary
    rows = service.subscribers()
    summary.subscribers = len(rows)
    due = [r for r in rows if is_due(r, now)][:MAX_EMAILS]
    summary.due = len(due)
    # Every alert ranks the searcher's own fields first, so every due row needs them.
    profiles = service.profiles([r["user_id"] for r in due]) if due else {}

    server = None
    try:
        for row in due:
            try:
                sections = sections_for(row, profiles.get(row["user_id"]), since_for(row))
            except Exception:  # noqa: BLE001 - one subscriber must not stop the rest
                logger.exception("could not build an alert")
                summary.failed += 1
                continue
            if not sections:
                summary.empty += 1
                continue
            message = compose(row, sections)
            if not sending:
                summary.sent += 1
                logger.info("would send %r", message["Subject"])
                continue
            try:
                server = server or _smtp()
                server.send_message(message)
                service.mark_sent(row["user_id"], now)
                summary.sent += 1
            except (smtplib.SMTPException, OSError, service.ServiceError):
                logger.exception("could not send an alert")
                summary.failed += 1
                server = None
    finally:
        if server is not None:
            try:
                server.quit()
            except smtplib.SMTPException:
                pass
    if not sending and summary.sent:
        summary.notes.append(f"{summary.sent} would have been sent")
    return summary

"""Delete old Vercel deployments of the site once they near the free plan's storage cap.

    python scripts/prune_vercel_deployments.py [--keep 5] [--high 90] [--low 60]
                                               [--deployment-mb 80] [--dry-run]

Reads VERCEL_TOKEN, VERCEL_ORG_ID and VERCEL_PROJECT_ID, the same variables the deploy
step uses.

Every crawl deploys a fresh bundle: the Python dependencies plus the 12 MB snapshot, tens
of megabytes each. Vercel keeps them, and on the free plan their bundles share a 10 GB
"Function Storage" allowance that hourly deploys filled in under a week (Vercel's limit
email of 2026-10-15). Over the cap, Vercel can refuse new deployments, and the site would
stop receiving crawls.

The rule: below `--high` percent of the cap, nothing happens. At or above it, the oldest
deployments are deleted until usage is at or below `--low` percent. The live deployment,
the `--keep` newest ready production deployments and any still building are never
deleted, even when that leaves usage above `--low`.

Vercel has no public API for usage, so usage is estimated: ready deployments times
`--deployment-mb`, calibrated against the Usage page.

Standard library only, so it runs before or without `pip install`.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.vercel.com"

# Deployments still being built may be the one the current run is about to promote.
UNFINISHED = {"BUILDING", "INITIALIZING", "QUEUED"}


def plan_deletions(
    deployments: list[dict],
    production_id: str,
    *,
    keep: int,
    deployment_mb: float,
    limit_mb: float,
    high: float,
    low: float,
) -> tuple[float, list[dict], float]:
    """Usage before as a percentage, the deployments to delete (oldest first), and usage
    after. Nothing is deleted below `high`."""
    ready = sum(1 for d in deployments if _state(d) == "READY")

    def usage(n: int) -> float:
        return 100 * n * deployment_mb / limit_mb

    before = usage(ready)
    if before < high:
        return before, [], before

    newest_ready_production = sorted(
        (d for d in deployments if d.get("target") == "production" and _state(d) == "READY"),
        key=lambda d: d.get("created", 0),
        reverse=True,
    )
    protected = {d["uid"] for d in newest_ready_production[:keep]} | {production_id}
    candidates = sorted(
        (d for d in deployments if d["uid"] not in protected and _state(d) not in UNFINISHED),
        key=lambda d: d.get("created", 0),
    )
    doomed = []
    for d in candidates:
        if usage(ready) <= low:
            break
        doomed.append(d)
        if _state(d) == "READY":
            ready -= 1
    return before, doomed, usage(ready)


def _state(deployment: dict) -> str:
    return deployment.get("state") or deployment.get("readyState") or ""


def _when(deployment: dict) -> str:
    created = datetime.datetime.fromtimestamp(deployment.get("created", 0) / 1000, datetime.UTC)
    return created.strftime("%Y-%m-%d %H:%M UTC")


def _request(method: str, path: str, token: str, **params: str) -> dict:
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, method=method, headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


def _all_deployments(token: str, team: str, project: str) -> list[dict]:
    deployments: list[dict] = []
    until: str | None = None
    while True:
        params = {"teamId": team, "projectId": project, "limit": "100"}
        if until:
            params["until"] = until
        page = _request("GET", "/v6/deployments", token, **params)
        deployments.extend(page.get("deployments", []))
        until = (page.get("pagination") or {}).get("next")
        if not until:
            return deployments
        until = str(until)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", type=int, default=5)
    parser.add_argument("--high", type=float, default=90, help="percent that starts a cleanup")
    parser.add_argument("--low", type=float, default=60, help="percent a cleanup aims for")
    parser.add_argument("--limit-gb", type=float, default=10)
    parser.add_argument("--deployment-mb", type=float, default=80)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = os.environ.get("VERCEL_TOKEN", "")
    team = os.environ.get("VERCEL_ORG_ID", "")
    project = os.environ.get("VERCEL_PROJECT_ID", "")
    if not (token and team and project):
        print("::warning::VERCEL_TOKEN, VERCEL_ORG_ID or VERCEL_PROJECT_ID is unset; nothing pruned.")
        return 1

    # Without the live deployment's id there is no safe way to tell what to keep, so stop
    # rather than guess: deleting the served deployment takes the site down.
    info = _request("GET", f"/v9/projects/{project}", token, teamId=team)
    production_id = ((info.get("targets") or {}).get("production") or {}).get("id")
    if not production_id:
        print("::warning::Could not find the live production deployment; nothing pruned.")
        return 1

    deployments = _all_deployments(token, team, project)
    plan = dict(
        keep=args.keep,
        deployment_mb=args.deployment_mb,
        limit_mb=args.limit_gb * 1000,
        low=args.low,
    )
    before, doomed, after = plan_deletions(deployments, production_id, high=args.high, **plan)
    ready = sum(1 for d in deployments if _state(d) == "READY")
    print(
        f"{len(deployments)} deployments ({ready} ready), live is {production_id}. "
        f"Estimated usage {before:.0f}% at {args.deployment_mb:g} MB each."
    )
    if not doomed:
        print(f"Below {args.high:g}%, nothing to delete.")
        if not args.dry_run:
            return 0
        # Show what a cleanup would remove if it ran now, so the list can be reviewed.
        _, doomed, after = plan_deletions(deployments, production_id, high=0, **plan)
        print("If a cleanup ran now:")

    if args.dry_run:
        gone = {d["uid"] for d in doomed}
        for d in sorted(deployments, key=lambda d: d.get("created", 0)):
            mark = "DELETE" if d["uid"] in gone else ("LIVE" if d["uid"] == production_id else "keep")
            print(f"  {mark:6} {_when(d)}  {_state(d):9} {d.get('target') or 'preview':10} {d['uid']}")
        print(f"Would delete {len(doomed)}, leaving an estimated {after:.0f}%.")
        return 0

    failed = 0
    for d in doomed:
        try:
            _request("DELETE", f"/v13/deployments/{d['uid']}", token, teamId=team)
        except urllib.error.HTTPError as exc:
            failed += 1
            print(f"::warning::Could not delete {d['uid']}: HTTP {exc.code}")
    print(f"Deleted {len(doomed) - failed} of {len(doomed)}, leaving an estimated {after:.0f}%.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

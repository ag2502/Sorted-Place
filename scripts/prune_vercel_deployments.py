"""Delete old Vercel deployments of the site, keeping the live one and a few before it.

    python scripts/prune_vercel_deployments.py [--keep 3] [--dry-run]

Reads VERCEL_TOKEN, VERCEL_ORG_ID and VERCEL_PROJECT_ID, the same variables the deploy
step uses.

Every crawl deploys a fresh bundle: the Python dependencies plus the 12 MB snapshot, tens
of megabytes each. Vercel keeps every deployment forever unless told otherwise, and on
the free plan their bundles all count toward a 10 GB "Function Storage" allowance. Hourly
deploys filled it in under a week (Vercel's limit email of 2026-10-15). Only the newest
deployment is ever served, so the rest are kept only as a few rollback points.

Standard library only, so it runs before or without `pip install`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.vercel.com"

# Deployments still being built may be the one the current run is about to promote.
UNFINISHED = {"BUILDING", "INITIALIZING", "QUEUED"}


def choose_deletions(deployments: list[dict], production_id: str, keep: int) -> list[dict]:
    """The deployments to delete: everything except the live production deployment, the
    `keep` newest ready production deployments, and any still building."""
    ready_production = sorted(
        (
            d
            for d in deployments
            if d.get("target") == "production" and _state(d) == "READY"
        ),
        key=lambda d: d.get("created", 0),
        reverse=True,
    )
    kept = {d["uid"] for d in ready_production[:keep]} | {production_id}
    return [
        d
        for d in deployments
        if d["uid"] not in kept and _state(d) not in UNFINISHED
    ]


def _state(deployment: dict) -> str:
    return deployment.get("state") or deployment.get("readyState") or ""


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
    parser.add_argument("--keep", type=int, default=3)
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
    doomed = choose_deletions(deployments, production_id, args.keep)
    print(f"{len(deployments)} deployments, deleting {len(doomed)}, live is {production_id}.")

    failed = 0
    for d in doomed:
        if args.dry_run:
            print(f"would delete {d['uid']} ({d.get('url')})")
            continue
        try:
            _request("DELETE", f"/v13/deployments/{d['uid']}", token, teamId=team)
        except urllib.error.HTTPError as exc:
            failed += 1
            print(f"::warning::Could not delete {d['uid']}: HTTP {exc.code}")
    if failed:
        print(f"::warning::{failed} of {len(doomed)} deletions failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

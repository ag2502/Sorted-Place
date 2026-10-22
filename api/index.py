"""Vercel entry point for the web finder.

Vercel serves a Python function by importing an ASGI `app` from `api/`. The package lives
under `src/`, which is not on the import path of a function that is not installed as a
distribution, so it is added here.

This module also pins the deployment to the committed snapshot. The function only ever
reads — no route in `jobfinder.web` writes, and the writers are the two GitHub Actions
workflows — so there is no correct database URL for it to inherit, and honouring one is a
liability: an inherited JOBFINDER_DATABASE_URL pointing at hosted Postgres put every page
load on the network, spent the egress allowance that ran out, and then made a database
outage indistinguishable from the site being down.

The snapshot is located by searching rather than by assuming one path. A bundled
function's layout is not the repository's: `__file__` and the working directory need not
share a root, so a single guessed path is the kind of thing that works locally and
silently falls back to the network in production.
"""

from __future__ import annotations

import gzip
import os
import shutil
import sys
import tempfile
from pathlib import Path

from fastapi import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_CANDIDATES = [
    ROOT / "data" / "jobfinder.db",
    Path.cwd() / "data" / "jobfinder.db",
    Path("/var/task/data/jobfinder.db"),
]
# What a deployment actually carries: the Vercel CLI will not upload a single file over
# 100 MB, so the crawl ships the snapshot gzipped (`export_snapshot.py --gzip`).
_PACKED = [p.with_name(p.name + ".gz") for p in _CANDIDATES]


def _unpack(packed: Path) -> Path | None:
    """Unpack the gzipped snapshot into the writable temp dir, once per container.

    Named by the packed file's size so a local run never reads a stale copy left by an
    older snapshot. Written under a temporary name and renamed, so a container that dies
    mid-write cannot leave a truncated database for the next start to trust.
    """
    target = Path(tempfile.gettempdir()) / f"jobfinder-{packed.stat().st_size}.db"
    if not target.exists():
        partial = target.with_name(f"{target.name}.{os.getpid()}.part")
        with gzip.open(packed, "rb") as src, partial.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        partial.replace(target)
    return target


SNAPSHOT = next((p for p in _CANDIDATES if p.exists()), None)
if SNAPSHOT is None:
    SNAPSHOT = next((_unpack(p) for p in _PACKED if p.exists()), None)

if SNAPSHOT is not None:
    os.environ["JOBFINDER_DATABASE_URL"] = (
        f"sqlite:///file:{SNAPSHOT}?mode=ro&immutable=1&uri=true"
    )
else:
    # Falling through to a network database is the failure this file exists to prevent,
    # so drop the inherited URL instead. The app then resolves its own default and the
    # pages fail fast and locally rather than hanging on a connection that cannot work.
    os.environ.pop("JOBFINDER_DATABASE_URL", None)

from jobfinder.web.app import app, owner_only  # noqa: E402


@app.get("/healthz", include_in_schema=False)
def healthz(request: Request) -> dict:
    """Report how the deployment resolved its data, without touching the network.

    Added because the deployed function's behaviour could not be explained from outside:
    some routes failed instantly and others hung for the full database timeout. Guessing
    from response times is slower than asking. Anyone may ask whether the snapshot was
    found, which is what an uptime check needs; the paths and errors behind the answer
    are the owner's (see `owner_only`).
    """
    if not owner_only(request):
        return {"ok": SNAPSHOT is not None}

    from sqlalchemy import func, select

    from jobfinder.core.config import settings
    from jobfinder.core.db import IS_READ_ONLY, session_scope
    from jobfinder.core.models import JobPosting

    info: dict = {
        "snapshot_found": SNAPSHOT is not None,
        "snapshot_path": str(SNAPSHOT) if SNAPSHOT else None,
        "candidates": {str(p): p.exists() for p in _CANDIDATES + _PACKED},
        "cwd": str(Path.cwd()),
        "entry_file": str(Path(__file__).resolve()),
        "url_scheme": settings.database_url.split(":")[0],
        "read_only": IS_READ_ONLY,
    }
    try:
        with session_scope() as s:
            info["active_jobs"] = s.execute(
                select(func.count()).select_from(JobPosting)
            ).scalar_one()
    except Exception as exc:  # the whole point is to see this text
        info["query_error"] = f"{type(exc).__name__}: {exc}"[:400]
    return info


__all__ = ["app"]

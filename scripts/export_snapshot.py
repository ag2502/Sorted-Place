"""Build the read-only SQLite snapshot that the website serves.

The finder never writes. Every route in `jobfinder.web` only reads, and the sole writers
are the two GitHub Actions workflows, which is why `crawl.yml` and `registry.yml` share a
concurrency group and the web app is not in it. A reader that never writes does not need
a live connection to the crawl's database — it needs the rows it can actually show, as a
file it can open locally.

That subset is small. Of ~24,000 active postings about 7,000 are in Ireland or remote,
and no query can return anything else: `_search_results` filters on `is_ireland`, or on
`is_ireland OR is_remote` when the searcher opts into remote. The rest is dead weight in a
file that has to ship with the deployment, so it is dropped here — 38 MB becomes ~12 MB,
about 2 MB gzipped.

Descriptions are kept even though no template renders one. `matching.rank` builds its
term documents from title plus description, and the dedup tiebreak in `web.app` prefers
the longest description among duplicate postings. Dropping the column would quietly
change which job wins a dedup group and how everything ranks, which is exactly the kind
of silent behaviour change that is painful to trace back to a build script.

The filter is applied in the *source* query rather than by copying everything and pruning
afterwards. When the source is Neon, the rows that are never sent are the whole point:
pulling all 5,300 would spend the same egress this snapshot exists to stop spending.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
from pathlib import Path

from sqlalchemy import create_engine, insert, select

from jobfinder.core.db import engine as source_engine
from jobfinder.core.models import Base

# SQLite variables per statement are capped (999 on older builds), and a job_postings row
# is 23 columns wide. 400 rows keeps every table well inside that ceiling.
CHUNK_ROWS = 400


def _precompute_skills(target) -> int:
    """Store the skills each advert names, so the site never has to find them itself.

    Finding them is 188 regexes over a full description, and doing it for every advert
    was what made the first search on a fresh Vercel instance take ten seconds. Here it
    runs once per build instead. The table is keyed by a hash of the exact text ranking
    reads, and carries a fingerprint of the skills list, so a snapshot built by other
    code is simply ignored (see `rank.preload_skills`).
    """
    from jobfinder.matching.rank import advert_hash, skills_fingerprint
    from jobfinder.normalize.taxonomy import extract_skills

    with target.begin() as dst:
        dst.exec_driver_sql(
            "create table advert_skills (advert_hash text primary key, skills text not null)"
        )
        rows = dst.exec_driver_sql("select title, description from job_postings").all()
        found: dict[str, str] = {}
        for title, description in rows:
            text = f"{title}\n{description or ''}"
            found[advert_hash(text)] = json.dumps(sorted(extract_skills(text)))
        found["fingerprint"] = skills_fingerprint()
        dst.exec_driver_sql(
            "insert into advert_skills (advert_hash, skills) values (?, ?)",
            list(found.items()),
        )
    return len(found) - 1


def _precompute_title_fields(target) -> int:
    """Store the fields each job title falls in, for the same reason as the skills.

    Keyed by the title itself, which is all `rank._title_fields` reads, and carrying a
    fingerprint of the field terms (see `rank.preload_title_fields`).
    """
    from jobfinder.matching.rank import title_fields_fingerprint
    from jobfinder.normalize.taxonomy import classify_title

    with target.begin() as dst:
        dst.exec_driver_sql(
            "create table title_fields (title text primary key, fields text not null)"
        )
        titles = {title for (title,) in dst.exec_driver_sql("select title from job_postings")}
        found = {title: json.dumps(classify_title(title)) for title in titles if title}
        # No title carries a NUL character, so this key cannot collide with a real one.
        found["\0fingerprint"] = title_fields_fingerprint()
        dst.exec_driver_sql(
            "insert into title_fields (title, fields) values (?, ?)", list(found.items())
        )
    return len(found) - 1


def _precompute_facts(target) -> int:
    """Store the work mode and pay each advert states, for the same reason again.

    Keyed by everything the readers look at (see `facts.reading_key`) and fingerprinted
    by the code that read them (see `facts.fingerprint`).
    """
    from jobfinder.normalize import facts

    with target.begin() as dst:
        dst.exec_driver_sql(
            "create table advert_facts (reading_key text primary key, facts text not null)"
        )
        rows = dst.exec_driver_sql("select description, is_remote from job_postings").all()
        found: dict[str, str] = {}
        for description, is_remote in rows:
            found[facts.reading_key(description, is_remote=bool(is_remote))] = facts.encode(
                facts.work_mode(description, is_remote=bool(is_remote)),
                facts.salary(description),
            )
        found["fingerprint"] = facts.fingerprint()
        dst.exec_driver_sql(
            "insert into advert_facts (reading_key, facts) values (?, ?)", list(found.items())
        )
    return len(found) - 1


def export(destination: Path) -> Path:
    """Copy the servable rows from the configured database into a fresh SQLite file."""
    if destination.exists():
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)

    target = create_engine(f"sqlite:///{destination}", future=True)
    Base.metadata.create_all(target)

    totals: dict[str, int] = {}
    with source_engine.connect() as src, target.begin() as dst:
        # sorted_tables is dependency-ordered, so parents land before the rows that
        # reference them and the foreign keys hold.
        for table in Base.metadata.sorted_tables:
            stmt = select(table)
            if table.name == "job_postings":
                stmt = stmt.where(
                    table.c.status == "ACTIVE",
                    table.c.is_ireland.is_(True)
                    | table.c.is_dublin.is_(True)
                    | table.c.is_remote.is_(True),
                )

            rows = [dict(row._mapping) for row in src.execute(stmt)]
            totals[table.name] = len(rows)
            for start in range(0, len(rows), CHUNK_ROWS):
                dst.execute(insert(table), rows[start : start + CHUNK_ROWS])

    totals["advert_skills"] = _precompute_skills(target)
    totals["title_fields"] = _precompute_title_fields(target)
    totals["advert_facts"] = _precompute_facts(target)

    # Reclaim the pages freed by everything that was not copied; without this the file
    # keeps the source's footprint and the size win disappears.
    # VACUUM cannot run inside a transaction, so it needs an autocommit connection
    # rather than the `begin()` block used for the copy.
    with target.connect().execution_options(isolation_level="AUTOCOMMIT") as dst:
        dst.exec_driver_sql("vacuum")
    target.dispose()

    for name, count in totals.items():
        print(f"  {name:<16} {count:>6} rows")
    print(f"snapshot: {destination} ({os.path.getsize(destination) / 1e6:.1f} MB)")
    return destination


def pack(snapshot: Path) -> Path:
    """Write a gzipped copy beside the snapshot, which is what a deployment uploads.

    The Vercel CLI refuses any single file over 100 MB on the Hobby plan, and the
    snapshot passed that on 2026-10-22 (142 MB) once ~600 more employers were seeded:
    every deploy after it failed. Gzipped it is about a fifth of the size, and
    `api/index.py` unpacks it on a cold start in well under a second.
    """
    packed = snapshot.with_name(snapshot.name + ".gz")
    with snapshot.open("rb") as src, gzip.open(packed, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    print(f"packed:   {packed} ({os.path.getsize(packed) / 1e6:.1f} MB)")
    return packed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "jobfinder.db",
        help="where to write the snapshot (default: data/jobfinder.db)",
    )
    parser.add_argument(
        "--gzip",
        action="store_true",
        help="also write <out>.gz, the copy a deployment uploads",
    )
    args = parser.parse_args()
    snapshot = export(args.out)
    if args.gzip:
        pack(snapshot)


if __name__ == "__main__":
    main()

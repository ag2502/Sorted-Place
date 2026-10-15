"""Which old deployments the Vercel cleanup deletes (scripts/prune_vercel_deployments.py).

Deleting the deployment the site is served from takes the site down, so the selection is
tested on its own, away from the network."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prune_vercel_deployments.py"
_spec = importlib.util.spec_from_file_location("prune_vercel_deployments", _PATH)
prune = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prune)


def _d(uid: str, created: int, state: str = "READY", target: str | None = "production") -> dict:
    return {"uid": uid, "created": created, "state": state, "target": target}


def _plan(deployments, live, keep=5, high=90, low=60):
    # 10 deployments of 100 MB in a 1000 MB cap: each one is 10%.
    before, doomed, after = prune.plan_deletions(
        deployments, live, keep=keep, deployment_mb=100, limit_mb=1000, high=high, low=low
    )
    return before, [d["uid"] for d in doomed], after


def _ten():
    return [_d(f"d{i}", i) for i in range(10)]


def test_does_nothing_below_the_high_mark():
    assert _plan(_ten()[:8], "d7") == (80, [], 80)


def test_deletes_oldest_first_down_to_the_low_mark():
    assert _plan(_ten(), "d9") == (100, ["d0", "d1", "d2", "d3"], 60)


def test_keeps_the_newest_five_even_if_that_stays_above_the_low_mark():
    before, doomed, after = _plan(_ten(), "d9", low=20)
    assert doomed == ["d0", "d1", "d2", "d3", "d4"]
    assert after == 50


def test_never_deletes_the_live_deployment_even_when_it_is_old():
    # After a rollback the live deployment is older than the ones after it.
    _, doomed, _ = _plan(_ten(), "d0", low=0)
    assert "d0" not in doomed


def test_leaves_deployments_still_building():
    deployments = _ten() + [_d("building", 10, state="BUILDING")]
    _, doomed, _ = _plan(deployments, "d9", low=0)
    assert "building" not in doomed


def test_failed_deployments_go_without_counting_toward_usage():
    deployments = [_d("failed", -1, state="ERROR")] + _ten()
    assert _plan(deployments, "d9") == (100, ["failed", "d0", "d1", "d2", "d3"], 60)


def test_reads_ready_state_when_state_is_absent():
    deployments = [{"uid": f"d{i}", "created": i, "readyState": "READY", "target": "production"} for i in range(10)]
    assert _plan(deployments, "d9")[1] == ["d0", "d1", "d2", "d3"]

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


def _deleted(deployments, production_id, keep):
    return {d["uid"] for d in prune.choose_deletions(deployments, production_id, keep)}


def test_keeps_the_newest_ready_production_deployments():
    deployments = [_d(f"d{i}", i) for i in range(10)]
    assert _deleted(deployments, "d9", keep=3) == {f"d{i}" for i in range(7)}


def test_never_deletes_the_live_deployment_even_when_it_is_not_the_newest():
    # After a rollback the live deployment is older than the ones after it.
    deployments = [_d(f"d{i}", i) for i in range(10)]
    assert "d2" not in _deleted(deployments, "d2", keep=3)


def test_leaves_deployments_still_building():
    deployments = [_d("old", 1), _d("live", 2), _d("building", 3, state="BUILDING")]
    assert _deleted(deployments, "live", keep=1) == {"old"}


def test_deletes_failed_and_preview_deployments():
    deployments = [
        _d("live", 5),
        _d("failed", 6, state="ERROR"),
        _d("preview", 7, target=None),
    ]
    assert _deleted(deployments, "live", keep=3) == {"failed", "preview"}


def test_reads_ready_state_when_state_is_absent():
    deployments = [{"uid": f"d{i}", "created": i, "readyState": "READY", "target": "production"} for i in range(4)]
    assert _deleted(deployments, "d3", keep=2) == {"d0", "d1"}

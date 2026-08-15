#!/usr/bin/env python3
"""Cron entry points for harvest/apply and nightly Gmail reconciliation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from hermes.client import Client
from hermes.gmail import scan
from hermes.worker import run as run_applications

ROOT = Path(__file__).resolve().parent.parent
ZONE = ZoneInfo(os.environ.get("HERMES_TIMEZONE", "America/Chicago"))


def scheduled_time(job: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now(ZONE)
    if job == "harvest_apply" and now.hour not in {11, 13, 15, 17, 19}:
        raise RuntimeError("harvest/apply is outside the allowed 11 AM–7 PM window")
    if job == "gmail" and now.hour != 20:
        raise RuntimeError("Gmail reconciliation is outside the 8 PM window")
    return now.replace(minute=0, second=0, microsecond=0)


def harvest() -> str:
    sys.path.insert(0, str(ROOT / "mcp"))
    import radar

    roles_path = ROOT / "data" / "roles.json"
    cache_path = ROOT / "data" / "descriptions.cache.json"
    data = json.loads(roles_path.read_text())
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    harvested, stats = radar.harvest(scope="priority")
    added, updated = radar.merge(data["roles"], harvested)
    radar.attach_descriptions(data["roles"], cache)
    for role in data["roles"]:
        radar.score_role(role, data["resumes"])
    data["roles"], dropped = radar.prune(data["roles"])
    cache.update(radar.split_descriptions(data["roles"]))
    data.setdefault("meta", {})["updated_by"] = "hermes"
    data["meta"]["generated"] = datetime.now(ZONE).isoformat()
    cache_path.write_text(json.dumps(cache) + "\n")
    roles_path.write_text(json.dumps(data, indent=2) + "\n")

    environment = os.environ.copy()
    environment["DATABASE_URL"] = environment.get(
        "HOST_DATABASE_URL", environment.get("DATABASE_URL", "")
    )
    subprocess.run(
        [sys.executable, str(ROOT / "db" / "sync.py"), "push"],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    return (
        f"boards={stats['ok']}/{stats['boards']} added={added} "
        f"updated={updated} dropped={dropped}"
    )


def execute(job: str) -> dict:
    when = scheduled_time(job)
    client = Client()
    started = client.post(
        "/runs/start", {"job": job, "scheduled_for": when.isoformat()}
    )["started"]
    if not started:
        return {"skipped": "duplicate scheduled run"}
    try:
        if job == "harvest_apply":
            harvest_detail = harvest()
            result = {"harvest": harvest_detail, "applications": run_applications()}
        elif job == "gmail":
            result = {"gmail": scan()}
        else:
            raise ValueError(f"unknown job {job}")
        client.post("/runs/finish", {
            "job": job,
            "scheduled_for": when.isoformat(),
            "outcome": "ok",
            "detail": json.dumps(result)[:1900],
        })
        return result
    except Exception as exc:
        client.post("/runs/finish", {
            "job": job,
            "scheduled_for": when.isoformat(),
            "outcome": "failed",
            "detail": f"{type(exc).__name__}: {exc}"[:1900],
        })
        raise


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"harvest_apply", "gmail"}:
        raise SystemExit("usage: python -m hermes.jobs [harvest_apply|gmail]")
    print(json.dumps(execute(sys.argv[1]), indent=2))

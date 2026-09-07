#!/usr/bin/env python3
"""Daily radar harvest, with nothing but the standard library and git.

The MCP `find_roles` tool and the Hermes cron both need something running —
an MCP client or the Docker stack. This needs neither, so launchd can run it
every morning and the board on the web is never more than a day old.

    uv run --python 3.12 python scripts/harvest.py            # priority scope
    uv run --python 3.12 python scripts/harvest.py --scope everything
    uv run --python 3.12 python scripts/harvest.py --no-push  # write, do not commit

Writes data/roles.json and data/descriptions.cache.json, then commits and
pushes so the published page picks it up.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))
import radar  # noqa: E402

ROLES = ROOT / "data" / "roles.json"
CACHE = ROOT / "data" / "descriptions.cache.json"


def run(scope: str, ats: str | None) -> str:
    data = json.loads(ROLES.read_text())
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    harvested, stats = radar.harvest(scope=scope, ats=ats)
    added, updated = radar.merge(data["roles"], harvested)
    radar.attach_descriptions(data["roles"], cache)
    for role in data["roles"]:
        radar.score_role(role, data["resumes"])
    data["roles"], dropped = radar.prune(data["roles"])
    cache.update(radar.split_descriptions(data["roles"]))
    data.setdefault("meta", {})["updated_by"] = "harvest.py"
    data["meta"]["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    CACHE.write_text(json.dumps(cache) + "\n")
    ROLES.write_text(json.dumps(data, indent=2) + "\n")
    apps = radar.count_applications(data["roles"])["total"]
    return (f"{stats['ok']}/{stats['boards']} sources ok, {stats['seen']} seen, "
            f"{stats['kept']} kept, +{added} new, {updated} known, {dropped} pruned, "
            f"{len(data['roles'])} on the board, {apps} applications")


def push(summary: str) -> None:
    for cmd in (["git", "add", "data/roles.json"],
                ["git", "commit", "-q", "-m", f"chore(radar): harvest {radar.today()} — {summary}"],
                ["git", "push", "-q"]):
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120)
        if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
            raise SystemExit(f"{' '.join(cmd)} failed: {(r.stderr or r.stdout).strip()}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", default="priority", choices=radar.SCOPES)
    ap.add_argument("--ats", default="", help="one source only, e.g. earlycareerradar")
    ap.add_argument("--no-push", action="store_true", help="write the file, skip git")
    args = ap.parse_args()
    summary = run(args.scope, args.ats or None)
    print(f"{datetime.now().isoformat(timespec='seconds')} {summary}")
    if not args.no_push:
        push(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

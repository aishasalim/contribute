#!/usr/bin/env python3
"""Print the application count and what needs a hand, from the terminal.

    uv run --python 3.12 python scripts/stats.py          # count + action items
    uv run --python 3.12 python scripts/stats.py --json   # machine-readable

Same definition as the `applications` MCP tool: one row per application in
data/roles.json with application.status != "none"; rejections count.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "mcp"))
import radar  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    data = json.loads((ROOT / "data" / "roles.json").read_text())
    roles = data["roles"]
    if args.json:
        print(json.dumps(radar.count_applications(roles)))
        return 0

    print(radar.applications_block(roles))
    active = [r for r in roles if r["application"]["status"] in ("in_progress", "phone_screen")]
    if active:
        print(f"\nIn progress ({len(active)}):")
        for r in sorted(active, key=lambda r: r["application"].get("applied") or ""):
            note = (r["application"].get("notes") or "")[:70]
            print(f"  {r['company']} — {r['title'][:55]}" + (f"  · {note}" if note else ""))
    flagged = [r for r in roles if str(r["application"].get("notes", "")).startswith("needs_human")]
    if flagged:
        print(f"\nNeeds you ({len(flagged)}):")
        for r in flagged:
            print(f"  {r['company']} — {r['title'][:55]}  · {r['application']['notes'][12:82]}")
    open_ = [r for r in roles if r["application"]["status"] == "none" and not r["dead"]]
    week = sum(1 for r in open_ if (radar.age_days(r) or 99) <= 7)
    strong = sum(1 for r in open_ if r["tier"] == "strong")
    latest = max((r.get("found") or "" for r in roles), default="never")
    print(f"\nBoard: {len(open_)} open · {strong} strong fit · {week} posted this week · "
          f"last harvest {latest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""sync — move the radar between data/roles.json and Postgres.

The database is the durable store. `roles` is a cache the harvest overwrites;
`applications` is your own state and a harvest never touches it. The page still
reads the JSON file, so `export` regenerates it after a pull.

    export DATABASE_URL='postgresql://user:pass@host:25060/db?sslmode=require'

    python3 db/sync.py init     # create the schema
    python3 db/sync.py push     # roles.json -> DB (applications inserted only if absent)
    python3 db/sync.py pull     # DB -> roles.json (DB application state wins)
    python3 db/sync.py stats    # counts, straight from SQL

Needs psycopg. No install step if you use uv:

    uv run --with 'psycopg[binary]' python db/sync.py push
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROLES = ROOT / "data" / "roles.json"
SCHEMA = Path(__file__).resolve().parent / "schema.sql"

APP_FIELDS = ("status", "applied", "resume", "recruiter", "network",
              "thank_you", "follow_up", "notes", "sheet_row")


def connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set.\n"
                 "DigitalOcean: Databases -> your cluster -> Connection details ->\n"
                 "  copy the 'Connection string' (it already carries ?sslmode=require).")
    try:
        import psycopg
    except ImportError:
        sys.exit("psycopg is missing. Run:\n"
                 "  uv run --with 'psycopg[binary]' python db/sync.py " + " ".join(sys.argv[1:]))
    return psycopg.connect(url)


def load() -> dict:
    return json.loads(ROLES.read_text())


def _role_row(r: dict) -> tuple:
    e = r.get("eligibility") or {}
    t = r.get("tracks") or {}
    return (
        r["id"], r["company"], r["title"], r.get("location"), r.get("workmode"),
        r.get("season"), r.get("url"), r.get("source"), r.get("posted") or None,
        r["found"], r.get("paid"), r.get("pay") or "",
        e.get("sponsorship"), e.get("citizenship"), e.get("class_year") or [],
        r.get("tags") or [], t.get("swe", 0), t.get("ml", 0), t.get("hwv", 0),
        r.get("best_track"), r.get("also_tracks") or [], r.get("tier"),
        r.get("why") or "", r.get("why_by") or "auto", r.get("snippet") or "",
        bool(r.get("dead")),
    )


ROLE_UPSERT = """
insert into roles (id, company, title, location, workmode, season, url, source,
    posted, found, paid, pay, sponsorship, citizenship, class_year, tags,
    score_swe, score_ml, score_hwv, best_track, also_tracks, tier, why, why_by,
    snippet, dead)
values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
on conflict (id) do update set
    company=excluded.company, title=excluded.title, location=excluded.location,
    workmode=excluded.workmode, season=excluded.season, url=excluded.url,
    source=excluded.source, posted=excluded.posted,
    paid=coalesce(excluded.paid, roles.paid),
    pay=case when excluded.pay <> '' then excluded.pay else roles.pay end,
    sponsorship=excluded.sponsorship, citizenship=excluded.citizenship,
    class_year=excluded.class_year, tags=excluded.tags,
    score_swe=excluded.score_swe, score_ml=excluded.score_ml,
    score_hwv=excluded.score_hwv, best_track=excluded.best_track,
    also_tracks=excluded.also_tracks, tier=excluded.tier,
    -- a human `why` is never overwritten by a machine draft
    why=case when roles.why_by='human' then roles.why else excluded.why end,
    why_by=case when roles.why_by='human' then 'human' else excluded.why_by end,
    snippet=excluded.snippet, dead=excluded.dead, last_seen=now()
    -- `found` and `first_seen` are deliberately absent: discovery date is sticky
"""

# Only seeds an application row that does not exist yet. Your own state wins.
APP_INSERT = """
insert into applications (role_id, status, applied, resume, recruiter, network,
    thank_you, follow_up, notes, sheet_row)
values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
on conflict (role_id) do nothing
"""


def cmd_init() -> None:
    with connect() as conn:
        conn.execute(SCHEMA.read_text())
        conn.commit()
    print("Schema applied.")


def cmd_push() -> None:
    data = load()
    roles = data["roles"]
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(ROLE_UPSERT, [_role_row(r) for r in roles])
        seeded = [r for r in roles if (r.get("application") or {}).get("status", "none") != "none"]
        cur.executemany(APP_INSERT, [(
            r["id"], *[(r["application"].get(f) or None) if f in ("applied", "resume", "sheet_row")
                       else r["application"].get(f, "" if f != "status" else "none")
                       for f in APP_FIELDS]) for r in seeded])
        conn.commit()
        cur.execute("select count(*) from roles")
        total = cur.fetchone()[0]
        cur.execute("select count(*) from applications where status <> 'none'")
        apps = cur.fetchone()[0]
    print(f"Pushed {len(roles)} roles ({total} in the table), "
          f"seeded {len(seeded)} applications ({apps} live).")
    print("Application rows that already existed were left alone.")


def cmd_pull() -> None:
    data = load()
    by_id = {r["id"]: r for r in data["roles"]}
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select role_id, status, applied, resume, recruiter, network,"
                    " thank_you, follow_up, notes, sheet_row from applications")
        rows = cur.fetchall()
    changed = 0
    for role_id, *vals in rows:
        role = by_id.get(role_id)
        if not role:
            continue
        app = role.setdefault("application", {})
        new = dict(zip(APP_FIELDS, vals))
        new["applied"] = new["applied"].isoformat() if new["applied"] else None
        if {k: app.get(k) for k in APP_FIELDS} != new:
            app.update(new)
            changed += 1
    data.setdefault("meta", {})["updated_by"] = "db"
    ROLES.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Pulled {len(rows)} application rows; {changed} role(s) changed in roles.json.")


def cmd_stats() -> None:
    q = {
        "roles":        "select count(*) from roles",
        "not dead":     "select count(*) from roles where not dead",
        "paid":         "select count(*) from roles where paid is true",
        "unpaid":       "select count(*) from roles where paid is false",
        "pay stated":   "select count(*) from roles where pay <> ''",
        "strong":       "select count(*) from roles where tier='strong'",
        "applied":      "select count(*) from applications where status='applied'",
        "rejected":     "select count(*) from applications where status='rejected'",
        "posted <=7d":  "select count(*) from radar where age_days <= 7",
    }
    with connect() as conn, conn.cursor() as cur:
        for label, sql in q.items():
            cur.execute(sql)
            print(f"  {label:<12} {cur.fetchone()[0]}")
        cur.execute("select best_track, tier, count(*) from roles"
                    " group by 1,2 order by 1,2")
        print("\n  track / tier:")
        for track, tier, n in cur.fetchall():
            print(f"    {track or '?':<4} {tier or '?':<8} {n}")


CMDS = {"init": cmd_init, "push": cmd_push, "pull": cmd_pull, "stats": cmd_stats}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in CMDS:
        sys.exit(f"usage: python3 db/sync.py [{'|'.join(CMDS)}]")
    CMDS[cmd]()

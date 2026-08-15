#!/usr/bin/env python3
"""On-demand application batch, invoked from Discord by the /apply skill.

The cron job in hermes.jobs is time-boxed and capped by APPLICATION_BATCH_LIMIT.
This entry point is deliberately neither: it is what runs when a human asks for
a batch right now, and it reports back in the shape that a chat message wants —
what got submitted, and what still needs a person.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from hermes import worker

DEFAULT_LIMIT = 10
MAX_LIMIT = 25


def _database_url() -> str:
    url = os.environ.get("HOST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("HOST_DATABASE_URL or DATABASE_URL is not configured")
    return url


def _attempts_since(since: datetime) -> list[dict]:
    with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
        return conn.execute(
            """select aa.state, aa.detail, aa.dry_run, aa.confirmation_url,
                      r.company, r.title, r.url
               from application_attempts aa join roles r on r.id = aa.role_id
               where aa.claimed_at >= %s
               order by aa.claimed_at""",
            [since],
        ).fetchall()


def _queue_depth() -> int:
    with psycopg.connect(_database_url(), row_factory=dict_row) as conn:
        return conn.execute("select count(*) as n from application_attempts").fetchone()["n"]


def _blockers(detail: str, keep: int = 4) -> str:
    """Turn the worker's semicolon-joined blocker dump into a short phrase."""
    named = [
        part.strip().rstrip("*")
        for part in (detail or "").split(";")
        if part.strip() and not part.strip().startswith("<unlabelled")
    ]
    unique = list(dict.fromkeys(named))
    if not unique:
        return "unlabelled fields the browser could not identify"
    shown = ", ".join(unique[:keep])
    return shown + ("…" if len(unique) > keep else "")


def summarise(attempts: list[dict], live: bool) -> str:
    submitted = [a for a in attempts if a["state"] == "submitted"]
    needs_you = [a for a in attempts if a["state"] == "awaiting_human"]
    unknown = [a for a in attempts if a["state"] == "unknown"]
    failed = [a for a in attempts if a["state"] in {"failed", "api_error"}]
    dry = [a for a in attempts if a["state"] == "abandoned"]

    mode = "LIVE — applications were submitted" if live else "DRY RUN — nothing was submitted"
    lines = [f"**Application batch finished.** {mode}.", ""]
    lines.append(
        f"{len(submitted)} submitted · {len(needs_you)} need you · "
        f"{len(unknown)} unverified · {len(failed)} failed · {len(dry)} dry-run passed"
    )

    if submitted:
        lines += ["", "**Submitted**"]
        for a in submitted:
            confirmation = f" — {a['confirmation_url']}" if a["confirmation_url"] else ""
            lines.append(f"• {a['company']} — {a['title']}{confirmation}")

    if needs_you:
        lines += ["", "**Needs you** (I stopped rather than guess)"]
        for a in needs_you:
            lines.append(f"• {a['company']} — {a['title']}")
            lines.append(f"   {_blockers(a['detail'])}")

    if unknown:
        lines += ["", "**Unverified** — I clicked submit but could not confirm it landed"]
        for a in unknown:
            lines.append(f"• {a['company']} — {a['title']} — check this one by hand")

    if failed:
        lines += ["", "**Failed**"]
        for a in failed:
            lines.append(f"• {a['company']} — {a['title']}: {(a['detail'] or '')[:160]}")

    if dry:
        lines += ["", "**Dry-run passed** — these would have been submitted with AUTO_SUBMIT=true"]
        for a in dry:
            lines.append(f"• {a['company']} — {a['title']}")

    if not attempts:
        lines += ["", "No role satisfied the apply policy, so I did not touch anything."]

    return "\n".join(lines)


def run_batch(limit: int = DEFAULT_LIMIT) -> str:
    limit = max(1, min(int(limit), MAX_LIMIT))
    # The canary list pins the cron job to two known roles. An explicit /apply
    # is a request for the real queue, so clear it for this process only.
    os.environ["CANARY_ROLE_IDS"] = ""
    live = os.environ.get("AUTO_SUBMIT", "false").lower() == "true"
    started = datetime.now(timezone.utc)
    worker.run(limit=limit)
    return summarise(_attempts_since(started), live)


if __name__ == "__main__":
    requested = DEFAULT_LIMIT
    if len(sys.argv) > 1:
        try:
            requested = int(sys.argv[1])
        except ValueError:
            raise SystemExit(f"usage: python -m hermes.apply_now [limit] (1-{MAX_LIMIT})")
    print(run_batch(requested))

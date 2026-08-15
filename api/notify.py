#!/usr/bin/env python3
"""Transactional Discord outbox producer and delivery worker."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import psycopg
from psycopg.rows import dict_row

from api.settings import settings

def queue_event(
    conn,
    event_type: str,
    role: dict,
    dedupe_key: str,
    *,
    attempt_id: str | None = None,
    detail: str = "",
    detail_url: str = "",
) -> None:
    """Insert in the caller's transaction so state and alert cannot diverge."""
    title = {
        "applied": "Hermes applied",
        "manual_applied": "You applied",
        "needs_human": "Needs you",
        "short_answer": "Quick answer needed",
        "human_handoff": "Application needs your voice",
        "review_confirmed": "Answer confirmed",
        "review_edited": "Answer updated",
        "review_declined": "Left for you",
        "dry_run": "Dry run complete",
        "failed": "Application failed",
        "harvest_failed": "Board refresh failed",
        "unknown": "Submission needs verification",
        "rejected": "Rejected",
        "offer": "Offer",
    }.get(event_type, "Application update")
    payload = {
        "title": f"{title} — {role.get('company', '?')}",
        "role": role.get("title", "?"),
        "company": role.get("company", "?"),
        "track": str(role.get("best_track") or "?").upper(),
        "url": role.get("url") or "",
        "detail": detail[:1000],
        "detail_url": detail_url,
        "dashboard": f"{settings.dashboard_url}#role={quote(str(role['id']), safe='')}",
        "event_type": event_type,
    }
    conn.execute(
        """insert into notification_outbox
           (event_type, role_id, attempt_id, dedupe_key, payload)
           values (%s,%s,%s,%s,%s::jsonb)
           on conflict (dedupe_key) do nothing""",
        [event_type, role["id"], attempt_id, dedupe_key, json.dumps(payload)],
    )


def queue_system_event(
    conn, event_type: str, dedupe_key: str, *, title: str, detail: str
) -> None:
    payload = {
        "title": title,
        "role": "Contribute automation",
        "company": "local Hermes",
        "track": "SYSTEM",
        "url": "",
        "detail": detail[:1000],
        "detail_url": "",
        "dashboard": settings.dashboard_url,
        "event_type": event_type,
    }
    conn.execute(
        """insert into notification_outbox(event_type,dedupe_key,payload)
           values (%s,%s,%s::jsonb) on conflict(dedupe_key) do nothing""",
        [event_type, dedupe_key, json.dumps(payload)],
    )


def _discord_target() -> str:
    if settings.hermes_discord_target:
        return settings.hermes_discord_target
    directory_path = (
        Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
        / "channel_directory.json"
    )
    try:
        directory = json.loads(directory_path.read_text())
        dms = [
            item for item in directory.get("platforms", {}).get("discord", [])
            if item.get("type") == "dm" and item.get("id")
        ]
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read Hermes channel directory: {exc}") from exc
    if len(dms) != 1:
        raise RuntimeError(
            "set HERMES_DISCORD_TARGET when Hermes has zero or multiple Discord DMs"
        )
    return f"discord:{dms[0]['id']}"


def _message(payload: dict) -> str:
    event_type = payload.get("event_type")
    role = payload.get("role", "?")
    company = payload.get("company", "?")
    track = payload.get("track", "?")
    if event_type == "applied":
        lines = [f"**I applied to the {track} role {role} at {company}.**"]
    elif event_type == "manual_applied":
        # You marked this one yourself; saying "I applied" here reads as though
        # Hermes did it, which is the opposite of reassuring.
        lines = [
            f"**You applied to the {track} role {role} at {company}.**",
            "Recorded in Contribute. I'll watch your inbox for a reply.",
        ]
    elif event_type == "short_answer":
        lines = [
            f"**I’m applying to the {track} role {role} at {company}.**",
            "I need you to confirm a short answer before I retry.",
        ]
    elif event_type == "human_handoff":
        lines = [
            f"**The {track} role {role} at {company} needs your input.**",
            "This application contains open-ended writing that should use your voice.",
        ]
    elif event_type == "dry_run":
        lines = [
            f"**Dry run complete for the {track} role {role} at {company}.**",
            "No application was submitted. Review the captured details before approving a live run.",
        ]
    elif event_type in {"failed", "unknown"}:
        lines = [
            f"**I couldn’t complete the {track} application for {role} at {company}.**",
            "No application was submitted. The technical details are stored in Contribute.",
        ]
    elif event_type == "harvest_failed":
        lines = [
            "**I couldn’t refresh the Contribute internship board.**",
            "The previous board data is still available; technical details were kept locally.",
        ]
    else:
        lines = [f"**{payload['title']}**", f"{role} at {company}"]
    if payload.get("url"):
        lines.append(f"Job posting: {payload['url']}")
    if payload.get("detail") and event_type not in {"failed", "unknown", "harvest_failed"}:
        lines.extend(["", payload["detail"]])
    if payload.get("dashboard"):
        lines.extend(["", f"Contribute: {payload['dashboard']}"])
    if payload.get("detail_url"):
        label = "Review and respond" if event_type in {"short_answer", "human_handoff"} else "Private details"
        lines.extend(["", f"{label}: {payload['detail_url']}"])
    return "\n".join(line for line in lines if line is not None)


def _deliver(payload: dict) -> None:
    configured = str(Path(settings.hermes_binary).expanduser())
    binary = shutil.which(configured) or str(Path.home() / ".local/bin/hermes")
    if not Path(binary).is_file():
        raise RuntimeError("Hermes CLI was not found; set HERMES_BINARY")
    result = subprocess.run(
        [binary, "send", "--to", _discord_target(), "--quiet"],
        input=_message(payload),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"hermes send failed ({result.returncode}): {result.stderr.strip()[:300]}"
        )


def deliver_once(limit: int = 20) -> int:
    """Claim due rows with SKIP LOCKED and retry failures with bounded backoff."""
    database_url = os.environ.get("HOST_DATABASE_URL", settings.database_url)
    if not database_url:
        raise RuntimeError("HOST_DATABASE_URL or DATABASE_URL is not configured")
    delivered = 0
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        rows = conn.execute(
            """select * from notification_outbox
               where delivered_at is null and next_attempt_at <= now()
               order by id for update skip locked limit %s""",
            [limit],
        ).fetchall()
        for row in rows:
            try:
                _deliver(row["payload"])
                conn.execute(
                    "update notification_outbox set delivered_at=now() where id=%s",
                    [row["id"]],
                )
                delivered += 1
            except Exception as exc:
                delay = min(3600, 30 * (2 ** min(row["attempts"], 7)))
                conn.execute(
                    """update notification_outbox
                       set attempts=attempts+1,
                           next_attempt_at=now()+(%s * interval '1 second'),
                           last_error=%s where id=%s""",
                    [delay, f"{type(exc).__name__}: {exc}"[:500], row["id"]],
                )
        conn.commit()
    return delivered


def worker() -> None:
    while True:
        try:
            deliver_once()
        except Exception as exc:
            print(
                f"{datetime.now(timezone.utc).isoformat()} outbox: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
        time.sleep(15)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker()
    else:
        print(deliver_once())

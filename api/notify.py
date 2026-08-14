#!/usr/bin/env python3
"""notify — Discord messages, so the human in the loop is a notification.

Every call is best-effort and silent on failure. A Discord outage must never
stop an application from being recorded: the database is the record, Discord is
only the ping.

    export DISCORD_WEBHOOK='https://discord.com/api/webhooks/...'
"""

from __future__ import annotations

import json
import os
import urllib.request

WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")
DASHBOARD = os.environ.get("DASHBOARD_URL", "https://aishasalim.github.io/contributie/radar.html")

BLUE, GREEN, AMBER, RED = 0x2F6FD6, 0x1F9D57, 0xB9770A, 0xD0555F
TRACK = {"swe": "SWE", "ml": "ML", "hwv": "HW Verif"}


def _send(embed: dict, content: str = "") -> None:
    if not WEBHOOK:
        return
    body = json.dumps({"content": content, "embeds": [embed]}).encode()
    req = urllib.request.Request(
        WEBHOOK, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass  # the record is in Postgres; a missed ping is not a failure


def _role_line(role: dict) -> str:
    url = role.get("url")
    title = role.get("title", "?")
    return f"[{title}]({url})" if url else title


def applied(role: dict, resume: str, actor: str = "hermes") -> None:
    who = "Hermes applied" if actor == "hermes" else "Applied"
    _send({
        "title": f"{who} — {role.get('company', '?')}",
        "description": _role_line(role),
        "color": GREEN,
        "fields": [{"name": "Resume sent", "value": TRACK.get(resume, resume), "inline": True}],
        "footer": {"text": "contributie radar"},
    })


def status_changed(role: dict, status: str, source: str = "manual") -> None:
    colour = RED if status == "rejected" else GREEN if status == "offer" else BLUE
    ping = "@here " if status == "offer" else ""
    _send({
        "title": f"{status.replace('_', ' ').title()} — {role.get('company', '?')}",
        "description": _role_line(role),
        "color": colour,
        "fields": [{"name": "Detected by", "value": source, "inline": True}],
        "footer": {"text": "contributie radar"},
    }, content=ping)


def needs_human(role: dict, reason: str) -> None:
    """Hermes stopped. This is the message that actually needs a reply."""
    _send({
        "title": f"Needs you — {role.get('company', '?')}",
        "description": _role_line(role),
        "color": AMBER,
        "fields": [
            {"name": "Why Hermes stopped", "value": reason[:1000]},
            {"name": "Dashboard", "value": DASHBOARD},
        ],
        "footer": {"text": "the application is still open — nothing was submitted"},
    }, content="<@&0>".replace("<@&0>", ""))


def fresh_digest(roles: list[dict], window_days: int = 3) -> None:
    """One daily message with the new strong fits, not one per role."""
    if not roles:
        return
    lines = []
    for r in roles[:15]:
        age = r.get("age_days")
        stamp = "today" if age == 0 else f"{age}d"
        lines.append(f"`{r.get('score', 0):>3}` {TRACK.get(r.get('best_track'), '?'):<8} "
                     f"**{r.get('company', '?')}** — {_role_line(r)} · {stamp}")
    more = f"\n\n+{len(roles) - 15} more on the dashboard." if len(roles) > 15 else ""
    _send({
        "title": f"{len(roles)} strong fit(s) posted in the last {window_days} days",
        "description": "\n".join(lines) + more,
        "color": BLUE,
        "fields": [{"name": "Dashboard", "value": DASHBOARD}],
        "footer": {"text": "contributie radar · daily digest"},
    })

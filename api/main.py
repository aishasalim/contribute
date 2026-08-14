#!/usr/bin/env python3
"""contributie API — the seam Hermes talks to.

A JSON file cannot be the interface for an agent that writes back. Two clients
(the dashboard and Hermes) both reading and writing one file in git is a merge
conflict waiting to happen, and Hermes cannot mark a role "applied" without a
round trip through a commit. So: Postgres behind a small HTTP API.

    GET  /health
    GET  /roles          filter + page the board (freshest first by default)
    GET  /roles/{id}
    GET  /queue          roles that pass the auto-apply contract — Hermes reads this
    POST /roles/{id}/claim    Hermes takes a role before it starts (prevents doubles)
    POST /roles/{id}/apply    Hermes reports a submitted application
    POST /roles/{id}/status   status change (e.g. a rejection found in email)
    POST /roles/{id}/flag     Hermes could not finish; hand it to a human
    GET  /stats

Reads are open when API_TOKEN is unset. Writes ALWAYS need
`Authorization: Bearer $API_TOKEN`, so nothing can move an application without
the token.

    export DATABASE_URL='postgresql://...?sslmode=require'
    export API_TOKEN='...'                    # required for writes
    export DISCORD_WEBHOOK='https://discord.com/api/webhooks/...'   # optional
    uv run --with fastapi --with uvicorn --with 'psycopg[binary]' \
        uvicorn api.main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date
from typing import Any, Literal

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

import notify

DATABASE_URL = os.environ.get("DATABASE_URL", "")
API_TOKEN = os.environ.get("API_TOKEN", "")

STATUSES = ("none", "applied", "in_progress", "phone_screen", "rejected", "offer")
TRACKS = ("swe", "ml", "hwv")

app = FastAPI(title="contributie", version="1.0",
              description="Internship radar — read the board, report applications.")

# The dashboard is served from GitHub Pages / Vercel, so it is cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@contextmanager
def db():
    if not DATABASE_URL:
        raise HTTPException(503, "DATABASE_URL is not configured")
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


def require_token(authorization: str = Header(default="")) -> None:
    """Writes always need the token. An unset API_TOKEN fails closed."""
    if not API_TOKEN:
        raise HTTPException(503, "API_TOKEN is not configured; writes are disabled")
    if authorization.removeprefix("Bearer ").strip() != API_TOKEN:
        raise HTTPException(401, "bad or missing bearer token")


# ------------------------------------------------------------------- read paths
@app.get("/health")
def health() -> dict:
    try:
        with db() as conn:
            n = conn.execute("select count(*) as n from roles").fetchone()["n"]
        return {"ok": True, "roles": n}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"database unreachable: {type(e).__name__}")


ROLE_COLS = """
    id, company, title, location, workmode, season, url, source, posted, found,
    paid, pay, sponsorship, citizenship, tags, score_swe, score_ml, score_hwv,
    best_track, also_tracks, tier, why, snippet, dead, status, applied, resume,
    notes, score, age_days
"""


@app.get("/roles")
def list_roles(
    track: str | None = Query(None, description="swe | ml | hwv"),
    tier: str | None = Query(None, description="strong | fit | stretch"),
    status: str | None = Query(None, description="none | applied | rejected | ..."),
    max_age: int | None = Query(None, ge=0, description="posted within N days"),
    min_score: int = Query(0, ge=0, le=100),
    paid_only: bool = True,
    include_dead: bool = False,
    q: str | None = Query(None, description="substring of company or title"),
    sort: Literal["fresh", "score"] = "fresh",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    """The board. Freshest first by default — a role posted today is worth more
    than a better-scoring one from six weeks ago."""
    where, args = ["true"], []
    if track:
        where.append("(best_track = %s or %s = any(also_tracks))")
        args += [track, track]
    if tier:
        where.append("tier = %s")
        args.append(tier)
    if status:
        where.append("status = %s")
        args.append(status)
    if max_age is not None:
        where.append("age_days <= %s")
        args.append(max_age)
    if min_score:
        where.append("score >= %s")
        args.append(min_score)
    if paid_only:
        where.append("paid is not false")
    if not include_dead:
        where.append("not dead")
    if q:
        where.append("(company ilike %s or title ilike %s)")
        args += [f"%{q}%", f"%{q}%"]

    order = ("age_days asc nulls last, score desc" if sort == "fresh"
             else "score desc, age_days asc nulls last")
    sql = (f"select {ROLE_COLS} from radar where {' and '.join(where)}"
           f" order by {order} limit %s offset %s")
    with db() as conn:
        rows = conn.execute(sql, args + [limit, offset]).fetchall()
        total = conn.execute(
            f"select count(*) as n from radar where {' and '.join(where)}", args
        ).fetchone()["n"]
    return {"total": total, "limit": limit, "offset": offset, "roles": rows}


@app.get("/roles/{role_id}")
def get_role(role_id: str) -> dict:
    with db() as conn:
        row = conn.execute(f"select {ROLE_COLS} from radar where id = %s",
                           [role_id]).fetchone()
        if not row:
            raise HTTPException(404, "no such role")
        row["events"] = conn.execute(
            "select from_status, to_status, at, note from application_events"
            " where role_id = %s order by at desc", [role_id]).fetchall()
    return row


@app.get("/queue")
def queue(limit: int = Query(20, ge=1, le=100), max_age: int = 14) -> dict:
    """What Hermes may apply to, and nothing else.

    This endpoint IS the auto-apply contract, enforced in SQL rather than left to
    the agent's judgement: strong tier, never applied, not dead, no citizenship
    bar, not explicitly unpaid, and recent. Freshest first.
    """
    with db() as conn:
        rows = conn.execute(f"""
            select {ROLE_COLS} from radar
            where tier = 'strong'
              and status = 'none'
              and not dead
              and citizenship is null
              and paid is not false
              and age_days <= %s
            order by age_days asc nulls last, score desc
            limit %s
        """, [max_age, limit]).fetchall()
    return {"count": len(rows), "roles": rows,
            "contract": {"tier": "strong", "status": "none", "dead": False,
                         "citizenship_bar": None, "unpaid": False,
                         "max_age_days": max_age,
                         "resume": "use best_track; never substitute another",
                         "free_text": "stop and POST /flag; never invent an answer"}}


# ------------------------------------------------------------------ write paths
class Apply(BaseModel):
    resume: Literal["swe", "ml", "hwv"]
    applied: date | None = None
    notes: str = ""
    actor: str = Field("hermes", description="who did it: hermes | human")


class StatusChange(BaseModel):
    status: Literal["none", "applied", "in_progress", "phone_screen", "rejected", "offer"]
    notes: str = ""
    source: str = Field("manual", description="manual | email | hermes")


class Flag(BaseModel):
    reason: str
    actor: str = "hermes"


def _upsert_application(conn, role_id: str, fields: dict[str, Any]) -> dict:
    row = conn.execute("select company, title, url, best_track from roles where id = %s",
                       [role_id]).fetchone()
    if not row:
        raise HTTPException(404, "no such role")
    cols = ", ".join(fields)
    ph = ", ".join(["%s"] * len(fields))
    updates = ", ".join(f"{k} = excluded.{k}" for k in fields)
    conn.execute(
        f"insert into applications (role_id, {cols}) values (%s, {ph})"
        f" on conflict (role_id) do update set {updates}",
        [role_id, *fields.values()])
    conn.commit()
    return row


@app.post("/roles/{role_id}/claim", dependencies=[Depends(require_token)])
def claim(role_id: str) -> dict:
    """Take a role before starting an application.

    Two Hermes runs must never apply to the same posting. The claim is a
    conditional write: it only succeeds when the status is still 'none', so the
    second caller gets 409 instead of a duplicate application.
    """
    with db() as conn:
        got = conn.execute("""
            insert into applications (role_id, status, notes)
            values (%s, 'in_progress', 'claimed by hermes')
            on conflict (role_id) do update set status = 'in_progress'
            where applications.status = 'none'
            returning role_id
        """, [role_id]).fetchone()
        conn.commit()
    if not got:
        raise HTTPException(409, "already claimed or already applied")
    return {"claimed": role_id}


@app.post("/roles/{role_id}/apply", dependencies=[Depends(require_token)])
def apply(role_id: str, body: Apply) -> dict:
    with db() as conn:
        role = conn.execute("select best_track from roles where id = %s",
                            [role_id]).fetchone()
        if not role:
            raise HTTPException(404, "no such role")
        if body.resume != role["best_track"]:
            raise HTTPException(
                422, f"resume must be the best track ({role['best_track']}); "
                     "the contract does not allow substituting another")
        row = _upsert_application(conn, role_id, {
            "status": "applied",
            "applied": body.applied or date.today(),
            "resume": body.resume,
            "notes": body.notes,
        })
    notify.applied(row, body.resume, body.actor)
    return {"ok": True, "role_id": role_id, "status": "applied", "resume": body.resume}


@app.post("/roles/{role_id}/status", dependencies=[Depends(require_token)])
def set_status(role_id: str, body: StatusChange) -> dict:
    with db() as conn:
        row = _upsert_application(conn, role_id, {
            "status": body.status,
            "notes": body.notes,
        })
    notify.status_changed(row, body.status, body.source)
    return {"ok": True, "role_id": role_id, "status": body.status}


@app.post("/roles/{role_id}/flag", dependencies=[Depends(require_token)])
def flag(role_id: str, body: Flag) -> dict:
    """Hermes could not finish on its own. Reset to 'none' so the role stays
    open, record why, and ping a human. Never guess an answer to keep going."""
    with db() as conn:
        row = _upsert_application(conn, role_id, {
            "status": "none",
            "notes": f"needs_human: {body.reason}",
        })
    notify.needs_human(row, body.reason)
    return {"ok": True, "role_id": role_id, "flagged": body.reason}


@app.get("/stats")
def stats() -> dict:
    with db() as conn:
        return {
            "roles": conn.execute("select count(*) as n from roles").fetchone()["n"],
            "fresh_3d": conn.execute(
                "select count(*) as n from radar where age_days <= 3 and tier <> 'none'"
            ).fetchone()["n"],
            "queue": conn.execute(
                "select count(*) as n from radar where tier='strong' and status='none'"
                " and not dead and citizenship is null and paid is not false"
            ).fetchone()["n"],
            "by_tier": conn.execute(
                "select tier, count(*) as n from roles group by 1 order by 1").fetchall(),
            "by_track": conn.execute(
                "select best_track, count(*) as n from roles group by 1 order by 1").fetchall(),
            "by_status": conn.execute(
                "select status, count(*) as n from applications group by 1 order by 1").fetchall(),
            "last_harvest": conn.execute(
                "select ran_at, scope, roles_kept, roles_new from harvests"
                " order by ran_at desc limit 1").fetchone(),
        }

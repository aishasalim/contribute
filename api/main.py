#!/usr/bin/env python3
"""Local API and policy boundary for the internship radar."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Literal

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from api import notify
from api.settings import settings

app = FastAPI(
    title="contribute",
    version="2.0",
    description="Internship radar and server-enforced Hermes workflow.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


@contextmanager
def db():
    if not settings.database_url:
        raise HTTPException(503, "DATABASE_URL is not configured")
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


def require_token(
    authorization: str = Header(default=""), key: str = Query(default="")
) -> None:
    # `key` lets the browser-rendered pages authenticate; they cannot set headers.
    if not settings.api_token:
        raise HTTPException(503, "API_TOKEN is not configured; writes are disabled")
    supplied = authorization.removeprefix("Bearer ").strip() or key.strip()
    if not hmac.compare_digest(supplied, settings.api_token):
        raise HTTPException(401, "bad or missing bearer token")


ROLE_COLS = """
 id, company, title, location, workmode, season, url, source, posted, found,
 paid, pay, sponsorship, citizenship, tags, score_swe, score_ml, score_hwv,
 best_track, also_tracks, tier, why, snippet, dead, status, applied, resume,
 notes, sheet_row, score, age_days,
 (select aa.state from application_attempts aa where aa.role_id=radar.id
  order by aa.claimed_at desc limit 1) as attempt_state,
 (select aa.detail from application_attempts aa where aa.role_id=radar.id
  order by aa.claimed_at desc limit 1) as attempt_detail,
 (select aa.updated_at from application_attempts aa where aa.role_id=radar.id
  order by aa.claimed_at desc limit 1) as attempt_updated_at
"""
QUEUE_SQL = """
 tier='strong' and status='none' and not dead and citizenship is null
 and paid is not false and age_days <= %s and season = any(%s)
 and not exists (
   select 1 from application_attempts aa
   where aa.role_id=radar.id
     and aa.state in ('awaiting_human','unknown')
 )
"""


def _role(conn, role_id: str, *, lock: bool = False) -> dict:
    if lock:
        locked = conn.execute(
            "select id from roles where id=%s for update", [role_id]
        ).fetchone()
        if not locked:
            raise HTTPException(404, "no such role")
    row = conn.execute(
        f"select {ROLE_COLS} from radar where id=%s", [role_id]
    ).fetchone()
    if not row:
        raise HTTPException(404, "no such role")
    return row


def _eligible(conn, role_id: str, *, lock: bool = False) -> dict:
    if lock:
        locked = conn.execute(
            "select id from roles where id=%s for update", [role_id]
        ).fetchone()
        if not locked:
            raise HTTPException(404, "no such role")
    row = conn.execute(
        f"""select {ROLE_COLS} from radar where id=%s and {QUEUE_SQL}""",
        [role_id, settings.max_age_days, list(settings.allowed_seasons)],
    ).fetchone()
    if not row:
        raise HTTPException(409, "role no longer satisfies the auto-apply policy")
    count = conn.execute(
        """select count(*) as n from applications a join roles r on r.id=a.role_id
           where lower(r.company)=lower(%s) and a.applied=current_date""",
        [row["company"]],
    ).fetchone()["n"]
    if count >= settings.company_daily_limit:
        raise HTTPException(409, "daily company application limit reached")
    return row


def _lease_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _normalized_question(text: str) -> str:
    return " ".join(text.lower().split())[:2000]


def _new_access_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, _lease_hash(token)


def _attempt(conn, attempt_id: str, lease_token: str, *, lock: bool = False) -> dict:
    suffix = " for update" if lock else ""
    row = conn.execute(
        f"select * from application_attempts where id=%s{suffix}", [attempt_id]
    ).fetchone()
    if not row or not hmac.compare_digest(row["lease_token_hash"], _lease_hash(lease_token)):
        raise HTTPException(401, "invalid attempt lease")
    if row["lease_expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(409, "attempt lease expired")
    return row


@app.get("/health")
def health() -> dict:
    try:
        with db() as conn:
            count = conn.execute("select count(*) as n from roles").fetchone()["n"]
        return {"ok": True, "roles": count}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"database unreachable: {type(exc).__name__}")


@app.get("/roles", dependencies=[Depends(require_token)])
def list_roles(
    track: str | None = None,
    tier: str | None = None,
    status: str | None = None,
    max_age: int | None = Query(None, ge=0),
    min_score: int = Query(0, ge=0, le=100),
    paid_only: bool = True,
    include_dead: bool = False,
    q: str | None = None,
    sort: Literal["fresh", "score"] = "fresh",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    where, args = ["true"], []
    if track:
        where.append("(best_track=%s or %s=any(also_tracks))")
        args += [track, track]
    if tier:
        where.append("tier=%s")
        args.append(tier)
    if status:
        where.append("status=%s")
        args.append(status)
    if max_age is not None:
        where.append("age_days<=%s")
        args.append(max_age)
    if min_score:
        where.append("score>=%s")
        args.append(min_score)
    if paid_only:
        where.append("paid is not false")
    if not include_dead:
        where.append("not dead")
    if q:
        where.append("(company ilike %s or title ilike %s)")
        args += [f"%{q}%", f"%{q}%"]
    order = (
        "age_days asc nulls last, score desc"
        if sort == "fresh" else "score desc, age_days asc nulls last"
    )
    predicate = " and ".join(where)
    with db() as conn:
        rows = conn.execute(
            f"select {ROLE_COLS} from radar where {predicate} order by {order} limit %s offset %s",
            args + [limit, offset],
        ).fetchall()
        total = conn.execute(
            f"select count(*) as n from radar where {predicate}", args
        ).fetchone()["n"]
    return {"total": total, "limit": limit, "offset": offset, "roles": rows}


@app.get("/roles/{role_id}", dependencies=[Depends(require_token)])
def get_role(role_id: str) -> dict:
    with db() as conn:
        return _role(conn, role_id)


@app.get("/roles/{role_id}/activity", dependencies=[Depends(require_token)])
def role_activity(role_id: str) -> dict:
    with db() as conn:
        _role(conn, role_id)
        attempts = conn.execute(
            """select id,state,resume,dry_run,detail,claimed_at,submitted_at,
                      confirmation_url,updated_at
               from application_attempts where role_id=%s order by claimed_at desc limit 10""",
            [role_id],
        ).fetchall()
        questions = conn.execute(
            """select attempt_id,normalized_text,field_type,required,category,
                      disposition,profile_key,answer_redacted,encountered_at
               from application_questions where role_id=%s
               order by encountered_at desc limit 100""",
            [role_id],
        ).fetchall()
        events = conn.execute(
            """select ae.attempt_id,ae.event,ae.detail,ae.at
               from attempt_events ae join application_attempts aa on aa.id=ae.attempt_id
               where aa.role_id=%s order by ae.at desc limit 100""",
            [role_id],
        ).fetchall()
    return {"role_id": role_id, "attempts": attempts, "questions": questions, "events": events}


@app.post("/manual/{role_id}/applied", dependencies=[Depends(require_token)])
def mark_manually_applied(role_id: str) -> dict:
    """Called straight from the dashboard; there is no separate page to visit."""
    with db() as conn:
        role = _role(conn, role_id, lock=True)
        if role["status"] == "applied":
            return {"ok": True, "status": "applied", "noop": True}
        if role["status"] != "none":
            raise HTTPException(409, f"cannot mark a {role['status']} role as applied")
        conn.execute(
            """insert into applications(role_id,status,applied,resume,notes)
               values (%s,'applied',current_date,%s,'marked manually from Contribute')
               on conflict(role_id) do update set
                 status='applied',applied=current_date,resume=excluded.resume,
                 notes=excluded.notes""",
            [role_id, role["best_track"]],
        )
        notify.queue_event(
            conn, "manual_applied", role, f"manual-applied:{role_id}:{date.today()}",
            detail=f"Marked manually. Resume: {role['best_track']}",
        )
        conn.commit()
    return {"ok": True, "role_id": role_id, "status": "applied"}


@app.get("/queue", dependencies=[Depends(require_token)])
def queue(limit: int = Query(20, ge=1, le=100)) -> dict:
    with db() as conn:
        rows = conn.execute(
            f"""select {ROLE_COLS} from radar where {QUEUE_SQL}
                order by age_days asc nulls last, score desc limit %s""",
            [settings.max_age_days, list(settings.allowed_seasons), limit],
        ).fetchall()
    return {
        "count": len(rows),
        "roles": rows,
        "contract": {
            "tier": "strong",
            "max_age_days": settings.max_age_days,
            "allowed_seasons": settings.allowed_seasons,
            "company_daily_limit": settings.company_daily_limit,
            "resume": "best_track",
        },
    }


@app.get("/roles/{role_id}/answer-overrides", dependencies=[Depends(require_token)])
def answer_overrides(role_id: str) -> dict:
    with db() as conn:
        _role(conn, role_id)
        rows = conn.execute(
            """select normalized_text,answer from application_answer_overrides
               where role_id=%s""",
            [role_id],
        ).fetchall()
    return {"role_id": role_id, "answers": {row["normalized_text"]: row["answer"] for row in rows}}


class ClaimBody(BaseModel):
    worker_id: str = "local-hermes"
    dry_run: bool = True


class LeaseBody(BaseModel):
    attempt_id: str
    lease_token: str


class Question(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    field_type: str = Field(max_length=100)
    required: bool = False
    category: str = Field(max_length=100)
    disposition: str = Field(max_length=100)
    profile_key: str | None = Field(None, max_length=200)
    answer_redacted: str | None = Field(None, max_length=500)
    answer_hash: str | None = Field(None, max_length=128)
    options: list[str] = Field(default_factory=list, max_length=300)
    proposed_answer: str | bool | int | float | None = None
    evidence: str | None = Field(None, max_length=500)
    blocker: str | None = Field(None, max_length=500)


class QuestionsBody(LeaseBody):
    questions: list[Question]


class StateBody(LeaseBody):
    state: Literal["preflight", "submitting", "failed", "unknown", "abandoned"]
    detail: str = Field("", max_length=2000)


class ApplyBody(LeaseBody):
    resume: Literal["swe", "ml", "hwv"]
    confirmation_number: str | None = Field(None, max_length=500)
    confirmation_url: str | None = Field(None, max_length=2000)


class FlagBody(LeaseBody):
    reason: str = Field(min_length=1, max_length=2000)
    questions: list[Question] = Field(default_factory=list, max_length=100)


def _detail_link(conn, attempt_id: str) -> str:
    token, token_hash = _new_access_token()
    conn.execute(
        """insert into application_detail_tokens(id,attempt_id,token_hash,expires_at)
           values (%s,%s,%s,now()+interval '90 days')""",
        [str(uuid.uuid4()), attempt_id, token_hash],
    )
    return f"{settings.review_base_url}/details/{token}"


@app.post("/roles/{role_id}/claim", dependencies=[Depends(require_token)])
def claim(role_id: str, body: ClaimBody) -> dict:
    attempt_id, lease = str(uuid.uuid4()), secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    with db() as conn:
        role = _eligible(conn, role_id, lock=True)
        try:
            conn.execute(
                """insert into application_attempts
                   (id,role_id,state,worker_id,lease_token_hash,lease_expires_at,resume,dry_run)
                   values (%s,%s,'claimed',%s,%s,%s,%s,%s)""",
                [attempt_id, role_id, body.worker_id, _lease_hash(lease), expires,
                 role["best_track"], body.dry_run],
            )
        except psycopg.errors.UniqueViolation:
            raise HTTPException(409, "role already has an active attempt")
        conn.execute(
            "insert into attempt_events(attempt_id,event,detail) values (%s,'claimed',%s)",
            [attempt_id, "dry-run" if body.dry_run else "live"],
        )
        conn.commit()
    return {
        "attempt_id": attempt_id,
        "lease_token": lease,
        "lease_expires_at": expires,
        "resume": role["best_track"],
        "role": role,
    }


@app.post("/roles/{role_id}/questions", dependencies=[Depends(require_token)])
def record_questions(role_id: str, body: QuestionsBody) -> dict:
    with db() as conn:
        attempt = _attempt(conn, body.attempt_id, body.lease_token, lock=True)
        if attempt["role_id"] != role_id:
            raise HTTPException(409, "attempt belongs to another role")
        for question in body.questions:
            redacted = None if question.category == "demographic" else question.answer_redacted
            conn.execute(
                """insert into application_questions
                   (attempt_id,role_id,normalized_text,field_type,required,category,
                    disposition,profile_key,answer_redacted,answer_hash)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [body.attempt_id, role_id, question.text, question.field_type,
                 question.required, question.category, question.disposition,
                 question.profile_key, redacted, question.answer_hash],
            )
        conn.execute(
            "insert into attempt_events(attempt_id,event,detail) values (%s,'questions_recorded',%s)",
            [body.attempt_id, f"{len(body.questions)} question(s)"],
        )
        conn.commit()
    return {"ok": True, "count": len(body.questions)}


@app.post("/roles/{role_id}/attempt-state", dependencies=[Depends(require_token)])
def attempt_state(role_id: str, body: StateBody) -> dict:
    with db() as conn:
        attempt = _attempt(conn, body.attempt_id, body.lease_token, lock=True)
        if attempt["role_id"] != role_id:
            raise HTTPException(409, "attempt belongs to another role")
        if body.state == "submitting":
            _eligible(conn, role_id, lock=True)
        conn.execute(
            "update application_attempts set state=%s,detail=%s,updated_at=now() where id=%s",
            [body.state, body.detail, body.attempt_id],
        )
        conn.execute(
            "insert into attempt_events(attempt_id,event,detail) values (%s,%s,%s)",
            [body.attempt_id, body.state, body.detail],
        )
        if body.state in {"unknown", "failed"} or (
            body.state == "abandoned" and attempt["dry_run"]
        ):
            role = _role(conn, role_id)
            detail_url = _detail_link(conn, body.attempt_id)
            event_type = "dry_run" if body.state == "abandoned" else body.state
            notify.queue_event(
                conn, event_type, role, f"{event_type}:{body.attempt_id}",
                attempt_id=body.attempt_id, detail=body.detail,
                detail_url=detail_url,
            )
        conn.commit()
    return {"ok": True, "state": body.state}


@app.post("/roles/{role_id}/apply", dependencies=[Depends(require_token)])
def apply(role_id: str, body: ApplyBody) -> dict:
    with db() as conn:
        attempt = _attempt(conn, body.attempt_id, body.lease_token, lock=True)
        if attempt["role_id"] != role_id or attempt["state"] != "submitting":
            raise HTTPException(409, "attempt is not in submitting state")
        role = _eligible(conn, role_id, lock=True)
        if body.resume != role["best_track"] or body.resume != attempt["resume"]:
            raise HTTPException(422, f"resume must be {role['best_track']}")
        if not body.confirmation_number and not body.confirmation_url:
            raise HTTPException(422, "positive submission confirmation is required")
        conn.execute(
            """insert into applications(role_id,status,applied,resume,notes)
               values (%s,'applied',current_date,%s,'submitted by hermes')
               on conflict(role_id) do update
               set status='applied',applied=current_date,resume=excluded.resume,
                   notes=excluded.notes
               where applications.status='none'""",
            [role_id, body.resume],
        )
        conn.execute(
            """update application_attempts set state='submitted',submitted_at=now(),
               confirmation_number=%s,confirmation_url=%s,updated_at=now() where id=%s""",
            [body.confirmation_number, body.confirmation_url, body.attempt_id],
        )
        detail_url = _detail_link(conn, body.attempt_id)
        notify.queue_event(
            conn, "applied", role, f"applied:{body.attempt_id}",
            attempt_id=body.attempt_id, detail=f"Resume: {body.resume}",
            detail_url=detail_url,
        )
        conn.commit()
    return {"ok": True, "role_id": role_id, "status": "applied"}


@app.post("/roles/{role_id}/flag", dependencies=[Depends(require_token)])
def flag(role_id: str, body: FlagBody) -> dict:
    with db() as conn:
        attempt = _attempt(conn, body.attempt_id, body.lease_token, lock=True)
        if attempt["role_id"] != role_id:
            raise HTTPException(409, "attempt belongs to another role")
        role = _role(conn, role_id, lock=True)
        if role["status"] != "none":
            raise HTTPException(409, "cannot flag a role with application history")
        conn.execute(
            """update application_attempts set state='awaiting_human',failure_code='needs_human',
               detail=%s,updated_at=now() where id=%s""",
            [body.reason, body.attempt_id],
        )
        conn.execute(
            """insert into applications(role_id,status,notes) values (%s,'none',%s)
               on conflict(role_id) do update set notes=excluded.notes
               where applications.status='none'""",
            [role_id, f"needs_human: {body.reason}"],
        )
        blocked = [
            question.model_dump(mode="json")
            for question in body.questions
            if question.disposition == "pending" or question.blocker
        ]
        kind = (
            "human_handoff"
            if any(question.get("category") == "free_text" for question in blocked)
            else "short_answer"
        )
        review_token, token_hash = _new_access_token()
        review_id = str(uuid.uuid4())
        payload = {
            "reason": body.reason,
            "questions": blocked,
            "role": {
                "company": role["company"],
                "title": role["title"],
                "track": role["best_track"],
                "url": role["url"],
            },
        }
        named_blockers = [
            str(question.get("text", "")).strip().rstrip("*")
            for question in blocked
            if question.get("text")
            and not str(question["text"]).startswith("<unlabelled")
        ]
        unique_named = list(dict.fromkeys(named_blockers))
        summary = (
            f"{len(blocked)} question(s) need review"
            + (f": {', '.join(unique_named[:3])}" if unique_named else "")
            + ("…" if len(unique_named) > 3 else "")
        )
        conn.execute(
            """insert into application_review_requests
               (id,attempt_id,role_id,token_hash,kind,payload,expires_at)
               values (%s,%s,%s,%s,%s,%s::jsonb,now()+interval '14 days')""",
            [review_id, body.attempt_id, role_id, token_hash, kind, json.dumps(payload)],
        )
        review_url = f"{settings.review_base_url}/review/{review_token}"
        notify.queue_event(
            conn, kind, role, f"needs-human:{body.attempt_id}",
            attempt_id=body.attempt_id, detail=summary,
            detail_url=review_url,
        )
        conn.commit()
    return {
        "ok": True,
        "role_id": role_id,
        "pending": body.reason,
        "kind": kind,
        "review_url": review_url,
    }


class ReviewDecision(BaseModel):
    action: Literal["confirm", "edit", "handle"]
    answers: dict[str, str | bool | int | float] = Field(default_factory=dict)


def _attempt_details(conn, attempt_id: str) -> dict:
    attempt = conn.execute(
        """select aa.*,r.company,r.title,r.location,r.url,r.best_track
           from application_attempts aa join roles r on r.id=aa.role_id
           where aa.id=%s""",
        [attempt_id],
    ).fetchone()
    if not attempt:
        raise HTTPException(404, "attempt not found")
    questions = conn.execute(
        """select normalized_text,field_type,required,category,disposition,profile_key,
                  answer_redacted,encountered_at
           from application_questions where attempt_id=%s order by id""",
        [attempt_id],
    ).fetchall()
    events = conn.execute(
        "select event,detail,at from attempt_events where attempt_id=%s order by id",
        [attempt_id],
    ).fetchall()
    return {"attempt": attempt, "questions": questions, "events": events}


def _details_html(details: dict, *, controls: str = "", heading: str = "") -> str:
    attempt = details["attempt"]
    title = heading or f"{attempt['title']} at {attempt['company']}"
    question_items = "".join(
        f"<li><strong>{escape(row['normalized_text'])}</strong>"
        f"<br><small>{escape(row['category'])} · {escape(row['disposition'])}</small></li>"
        for row in details["questions"]
    ) or "<li>No questions recorded.</li>"
    event_items = "".join(
        f"<li><strong>{escape(row['event'])}</strong> — {escape(row['detail'] or '')}</li>"
        for row in details["events"]
    ) or "<li>No events recorded.</li>"
    return f"""<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>
body{{font:16px system-ui;max-width:760px;margin:auto;padding:20px;line-height:1.45}}
.card{{border:1px solid #ccc;border-radius:12px;padding:16px;margin:14px 0}}
button,input,select,textarea{{font:inherit;padding:12px;margin:6px 0;width:100%;box-sizing:border-box}}
button{{cursor:pointer}} .danger{{background:#762323;color:white}} small{{color:#555}}
</style></head><body>
<h1>{escape(title)}</h1>
<div class="card"><strong>{escape(attempt['best_track'].upper())}</strong> ·
{escape(attempt['location'] or 'Location not listed')}<br>
State: {escape(attempt['state'])}<br>
<a href="{escape(attempt['url'] or '#')}">Open job posting</a></div>
{controls}
<div class="card"><h2>Application questions</h2><ul>{question_items}</ul></div>
<div class="card"><h2>History</h2><ul>{event_items}</ul></div>
</body></html>"""


def _review_row(conn, token: str, *, lock: bool = False) -> dict:
    suffix = " for update" if lock else ""
    row = conn.execute(
        f"select * from application_review_requests where token_hash=%s{suffix}",
        [_lease_hash(token)],
    ).fetchone()
    if not row:
        raise HTTPException(404, "review link not found")
    if row["expires_at"] <= datetime.now(timezone.utc):
        raise HTTPException(410, "review link expired")
    return row


@app.get("/review/{token}", response_class=HTMLResponse)
def review_page(token: str) -> HTMLResponse:
    with db() as conn:
        review = _review_row(conn, token)
        details = _attempt_details(conn, review["attempt_id"])
    payload = review["payload"]
    fields = []
    for index, question in enumerate(payload.get("questions", [])):
        text = str(question.get("text") or "Unlabelled question")
        proposed = question.get("proposed_answer")
        if isinstance(proposed, bool):
            proposed = "Yes" if proposed else "No"
        options = [str(value) for value in question.get("options", [])]
        if options:
            choices = "".join(
                f'<option value="{escape(value)}"'
                f'{" selected" if str(proposed) == value else ""}>{escape(value)}</option>'
                for value in options
            )
            control = f'<select data-question="{escape(text)}">{choices}</select>'
        else:
            control = (
                f'<textarea data-question="{escape(text)}" rows="2">'
                f"{escape(str(proposed or ''))}</textarea>"
            )
        fields.append(
            f'<div class="card"><strong>{escape(text)}</strong>{control}'
            f"<small>{escape(str(question.get('evidence') or 'No sourced answer available.'))}</small></div>"
        )
    state_note = f"<p>Review state: <strong>{escape(review['state'])}</strong></p>"
    if review["state"] != "pending":
        controls = state_note
    elif review["kind"] == "human_handoff":
        controls = (
            state_note + "".join(fields)
            + '<button class="danger" onclick="decide(\'handle\')">I’ll handle this application</button>'
        )
    else:
        controls = (
            state_note + "".join(fields)
            + '<button onclick="decide(\'confirm\')">Confirm and retry</button>'
            + '<button onclick="decide(\'edit\')">Save edited answers and retry</button>'
            + '<button class="danger" onclick="decide(\'handle\')">Decline — I’ll handle it</button>'
        )
    if review["state"] == "pending":
        controls += f"""<p id="result"></p><script>
async function decide(action) {{
  const answers = {{}};
  document.querySelectorAll('[data-question]').forEach(
    el => answers[el.dataset.question] = el.value
  );
  const response = await fetch('/review/{escape(token)}/decision', {{
    method:'POST', headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{action,answers}})
  }});
  const body = await response.json();
  document.getElementById('result').textContent =
    response.ok ? body.message : (body.detail || 'Request failed');
  if (response.ok) setTimeout(() => location.reload(), 800);
}}
</script>"""
    return HTMLResponse(_details_html(details, controls=controls, heading="Application review"))


@app.post("/review/{token}/decision")
def review_decision(token: str, body: ReviewDecision) -> dict:
    with db() as conn:
        review = _review_row(conn, token, lock=True)
        if review["state"] != "pending":
            return {"ok": True, "state": review["state"], "message": "Already resolved."}
        payload = review["payload"]
        if body.action in {"confirm", "edit"}:
            if review["kind"] == "human_handoff":
                raise HTTPException(409, "open-ended applications require you to handle them")
            for question in payload.get("questions", []):
                text = str(question.get("text") or "").strip()
                answer = body.answers.get(text, question.get("proposed_answer"))
                if answer in (None, ""):
                    raise HTTPException(422, f"answer required for {text or 'question'}")
                conn.execute(
                    """insert into application_answer_overrides
                       (role_id,normalized_text,answer,source_review_id)
                       values (%s,%s,%s::jsonb,%s)
                       on conflict(role_id,normalized_text) do update
                       set answer=excluded.answer,source_review_id=excluded.source_review_id,
                           created_at=now()""",
                    [
                        review["role_id"], _normalized_question(text),
                        json.dumps(answer), review["id"],
                    ],
                )
            state = "edited" if body.action == "edit" else "confirmed"
            message = "Answer saved. Hermes will retry this role on the next run."
        else:
            state = "declined"
            message = "Okay. Hermes will leave this application for you to handle."
        conn.execute(
            """update application_review_requests
               set state=%s,resolved_at=now() where id=%s""",
            [state, review["id"]],
        )
        conn.execute(
            """update application_attempts set state='abandoned',
               detail=%s,updated_at=now() where id=%s""",
            [f"review {state}", review["attempt_id"]],
        )
        conn.execute(
            """update applications set notes=%s
               where role_id=%s and status='none'""",
            [
                "answer approved; queued for Hermes retry"
                if state in {"confirmed", "edited"}
                else "human will handle this application",
                review["role_id"],
            ],
        )
        conn.execute(
            "insert into attempt_events(attempt_id,event,detail) values (%s,%s,%s)",
            [review["attempt_id"], f"review_{state}", message],
        )
        role = _role(conn, review["role_id"])
        notify.queue_event(
            conn, f"review_{state}", role, f"review:{review['id']}:{state}",
            attempt_id=review["attempt_id"], detail=message,
            detail_url=f"{settings.review_base_url}/review/{token}",
        )
        conn.commit()
    return {"ok": True, "state": state, "message": message}


@app.get("/details/{token}", response_class=HTMLResponse)
def attempt_details_page(token: str) -> HTMLResponse:
    with db() as conn:
        row = conn.execute(
            """select * from application_detail_tokens
               where token_hash=%s and expires_at>now()""",
            [_lease_hash(token)],
        ).fetchone()
        if not row:
            raise HTTPException(404, "detail link not found or expired")
        details = _attempt_details(conn, row["attempt_id"])
    return HTMLResponse(_details_html(details))


class StatusBody(BaseModel):
    status: Literal["in_progress", "phone_screen", "rejected", "offer"]
    notes: str = Field("", max_length=2000)
    source: str = Field("email", max_length=50)
    evidence_id: str | None = Field(None, max_length=500)


TRANSITIONS = {
    "applied": {"in_progress", "phone_screen", "rejected", "offer"},
    "in_progress": {"phone_screen", "rejected", "offer"},
    "phone_screen": {"rejected", "offer"},
    "rejected": set(),
    "offer": set(),
}


@app.post("/roles/{role_id}/status", dependencies=[Depends(require_token)])
def set_status(role_id: str, body: StatusBody) -> dict:
    with db() as conn:
        role = _role(conn, role_id, lock=True)
        current = role["status"]
        if current == body.status:
            return {"ok": True, "role_id": role_id, "status": current, "noop": True}
        if body.status not in TRANSITIONS.get(current, set()):
            raise HTTPException(409, f"invalid status transition {current} -> {body.status}")
        conn.execute(
            "update applications set status=%s,notes=%s where role_id=%s",
            [body.status, body.notes, role_id],
        )
        event_type = body.status if body.status in {"rejected", "offer"} else "status"
        notify.queue_event(
            conn, event_type, role,
            f"status:{role_id}:{body.status}:{body.evidence_id or date.today()}",
            detail=f"Detected by {body.source}. {body.notes}",
        )
        conn.commit()
    return {"ok": True, "role_id": role_id, "status": body.status}


class RunBody(BaseModel):
    job: Literal["harvest_apply", "gmail"]
    scheduled_for: datetime


class RunFinishBody(RunBody):
    outcome: Literal["ok", "failed", "skipped"]
    detail: str = Field("", max_length=2000)


class EmailObservationBody(BaseModel):
    message_id: str = Field(min_length=1, max_length=500)
    received_at: datetime
    classification: Literal[
        "rejected", "phone_screen", "in_progress", "offer", "received", "other"
    ]
    matched_role_id: str | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field("", max_length=2000)
    decision: Literal["update", "pending", "ignore"]


@app.post("/runs/start", dependencies=[Depends(require_token)])
def run_start(body: RunBody) -> dict:
    with db() as conn:
        row = conn.execute(
            """insert into scheduled_runs(job,scheduled_for) values (%s,%s)
               on conflict do nothing returning job""",
            [body.job, body.scheduled_for],
        ).fetchone()
        conn.commit()
    return {"started": bool(row)}


@app.post("/runs/finish", dependencies=[Depends(require_token)])
def run_finish(body: RunFinishBody) -> dict:
    with db() as conn:
        conn.execute(
            """update scheduled_runs set finished_at=now(),outcome=%s,detail=%s
               where job=%s and scheduled_for=%s""",
            [body.outcome, body.detail, body.job, body.scheduled_for],
        )
        if body.outcome == "failed":
            notify.queue_system_event(
                conn,
                "harvest_failed" if body.job == "harvest_apply" else "failed",
                f"run-failed:{body.job}:{body.scheduled_for.isoformat()}",
                title=f"{body.job} failed",
                detail=body.detail,
            )
        conn.commit()
    return {"ok": True}


@app.post("/email-observations", dependencies=[Depends(require_token)])
def email_observation(body: EmailObservationBody) -> dict:
    with db() as conn:
        inserted = conn.execute(
            """insert into email_observations
               (message_id,received_at,classification,matched_role_id,confidence,evidence,decision)
               values (%s,%s,%s,%s,%s,%s,%s)
               on conflict(message_id) do nothing returning message_id""",
            [body.message_id, body.received_at, body.classification,
             body.matched_role_id, body.confidence, body.evidence, body.decision],
        ).fetchone()
        if not inserted:
            return {"ok": True, "duplicate": True}
        if body.decision == "update":
            if not body.matched_role_id or body.confidence < 0.9:
                raise HTTPException(422, "status updates require one match at confidence >= 0.9")
            if body.classification not in {"rejected", "phone_screen", "in_progress", "offer"}:
                raise HTTPException(422, "classification does not represent a status change")
            role = _role(conn, body.matched_role_id, lock=True)
            current = role["status"]
            target = body.classification
            if current != target and target not in TRANSITIONS.get(current, set()):
                conn.execute(
                    "update email_observations set decision='pending' where message_id=%s",
                    [body.message_id],
                )
                notify.queue_event(
                    conn, "needs_human", role, f"email-pending:{body.message_id}",
                    detail=f"Refused status transition {current} -> {target}: {body.evidence}",
                )
                conn.commit()
                return {"ok": True, "duplicate": False, "decision": "pending"}
            if current != target:
                conn.execute(
                    "update applications set status=%s,notes=%s where role_id=%s",
                    [target, f"gmail: {body.evidence}", body.matched_role_id],
                )
                event_type = target if target in {"rejected", "offer"} else "status"
                notify.queue_event(
                    conn, event_type, role, f"email:{body.message_id}",
                    detail=f"Gmail match ({body.confidence:.0%}): {body.evidence}",
                )
        elif body.decision == "pending" and body.matched_role_id:
            role = _role(conn, body.matched_role_id)
            notify.queue_event(
                conn, "needs_human", role, f"email-pending:{body.message_id}",
                detail=f"Ambiguous Gmail status: {body.evidence}",
            )
        conn.commit()
    return {"ok": True, "duplicate": False, "decision": body.decision}


@app.get("/stats", dependencies=[Depends(require_token)])
def stats() -> dict:
    with db() as conn:
        return {
            "roles": conn.execute("select count(*) as n from roles").fetchone()["n"],
            "queue": conn.execute(
                f"select count(*) as n from radar where {QUEUE_SQL}",
                [settings.max_age_days, list(settings.allowed_seasons)],
            ).fetchone()["n"],
            "pending": conn.execute(
                "select count(*) as n from application_attempts where state='awaiting_human'"
            ).fetchone()["n"],
            "by_status": conn.execute(
                "select status,count(*) as n from applications group by 1 order by 1"
            ).fetchall(),
            "last_runs": conn.execute(
                """select distinct on(job) job,scheduled_for,finished_at,outcome,detail
                   from scheduled_runs order by job,scheduled_for desc"""
            ).fetchall(),
        }

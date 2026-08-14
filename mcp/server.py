#!/usr/bin/env python3
"""contributie — MCP server for tracking open-source contributions.

Backs a single JSON file (../data/contributions.json) that is the source of
truth for the tracker and dashboard. Reads live GitHub state via the `gh` CLI
(already authenticated), so there is no token to configure.

Tools:
  summary()                      -> counts + what needs attention
  list_contributions(status)     -> filtered records (merged|review|awaiting|closed|active|all)
  refresh(id=None)               -> pull live status/comments/diff from GitHub, write the file
  pr_context(id)                 -> title/body/diff/files so a recap can be (re)written
  set_recap(id, recap)           -> store the human-readable "what this does" recap
  list_potential(kind)           -> potential PRs to pick up + issues to watch
  add_potential_pr(...)          -> add a ready-to-pick-up lead
  add_potential_issue(...)       -> add a watchlist lead
  find_issues(repo, query, limit)-> live-search open, unassigned issues in a repo (to curate from)
  sync(message)                  -> git commit + push the repo so data/dashboard go live

Designed as the "seam" for Hermes: any writer that preserves the JSON schema
(meta + contributions[] + potential_prs[] + potential_issues[]) can take over.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
import radar  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "contributions.json"
ROLES = ROOT / "data" / "roles.json"
CACHE = ROOT / "data" / "descriptions.cache.json"

mcp = FastMCP("contributie")


# ---------------------------------------------------------------- data helpers
def load() -> dict:
    return json.loads(DATA.read_text())


def save(data: dict, updated_by: str = "mcp") -> None:
    data.setdefault("meta", {})
    data["meta"]["updated_by"] = updated_by
    data["meta"]["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    DATA.write_text(json.dumps(data, indent=2) + "\n")


def gh_json(args: list[str]) -> dict | list:
    """Run a gh command and parse JSON stdout. Raises on failure."""
    out = subprocess.run(
        ["gh", *args], capture_output=True, text=True, timeout=60
    )
    if out.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {out.stderr.strip()}")
    return json.loads(out.stdout) if out.stdout.strip() else {}


def _date(ts: str | None) -> str | None:
    return ts[:10] if ts else None


def _status_from(view: dict) -> str:
    state = view.get("state")
    if state == "MERGED":
        return "merged"
    if state == "CLOSED":
        return "closed"
    # OPEN: distinguish "in review" from "awaiting first review"
    reviews = view.get("reviews") or []
    human_reviews = [r for r in reviews if r.get("author", {}).get("login") != view.get("_owner")]
    return "review" if human_reviews else "awaiting"


def _latest_comment(view: dict) -> dict | None:
    events = []
    for c in view.get("comments") or []:
        events.append((c.get("createdAt"), c.get("author", {}).get("login"), c.get("body", "")))
    for r in view.get("reviews") or []:
        if r.get("body") or r.get("state"):
            events.append((r.get("submittedAt"), r.get("author", {}).get("login"),
                           r.get("body") or f"[review: {r.get('state')}]"))
    events = [e for e in events if e[0]]
    if not events:
        return None
    ts, author, body = max(events, key=lambda e: e[0])
    return {"author": author, "at": _date(ts), "body": (body or "").strip()[:240]}


def _refresh_one(rec: dict) -> str:
    """Update one contribution record in place from GitHub. Returns a change note."""
    num = rec["id"]
    repo = rec["repo"]
    view = gh_json([
        "pr", "view", str(num), "--repo", repo, "--json",
        "state,reviewDecision,mergedAt,closedAt,additions,deletions,changedFiles,comments,reviews",
    ])
    view["_owner"] = load()["meta"].get("owner")
    before = (rec.get("status"), rec.get("review_decision"))
    rec["status"] = _status_from(view)
    rec["review_decision"] = view.get("reviewDecision") or ""
    rec["additions"] = view.get("additions", rec.get("additions"))
    rec["deletions"] = view.get("deletions", rec.get("deletions"))
    rec["files"] = view.get("changedFiles", rec.get("files"))
    rec["ended"] = _date(view.get("mergedAt")) or _date(view.get("closedAt"))
    lc = _latest_comment(view)
    if lc:
        rec["latest_comment"] = lc
    after = (rec["status"], rec["review_decision"])
    flag = " ⚠ recap empty" if not rec.get("recap") else ""
    changed = " → CHANGED" if before != after else ""
    return f"#{num} {rec['project']}: {before[0]} → {after[0]}{changed}{flag}"


# ----------------------------------------------------------------------- tools
@mcp.tool()
def summary() -> str:
    """Quick overview: counts by status and what needs your attention next."""
    d = load()
    c = d["contributions"]
    by = {}
    for r in c:
        by[r["status"]] = by.get(r["status"], 0) + 1
    needs = [f"  • #{r['id']} {r['project']}: {r.get('next_action', '?')}"
             for r in c if r["status"] in ("review", "awaiting")]
    lines = [
        f"Contributie — {d['meta'].get('owner')}",
        f"Authored: {len(c)}  |  merged: {by.get('merged', 0)}  "
        f"| in review: {by.get('review', 0)}  | awaiting: {by.get('awaiting', 0)}  "
        f"| closed: {by.get('closed', 0)}",
        f"Potential PRs to pick up: {len(d.get('potential_prs', []))}  "
        f"| issues to watch: {len(d.get('potential_issues', []))}",
        f"Last updated: {d['meta'].get('generated')} by {d['meta'].get('updated_by')}",
    ]
    if needs:
        lines.append("Ball in your / maintainer's court:")
        lines += needs
    return "\n".join(lines)


@mcp.tool()
def list_contributions(status: str = "all") -> str:
    """List contribution records. status: all | active | merged | review | awaiting | closed."""
    d = load()
    recs = d["contributions"]
    if status == "active":
        recs = [r for r in recs if r["status"] in ("review", "awaiting")]
    elif status != "all":
        recs = [r for r in recs if r["status"] == status]
    if not recs:
        return f"No contributions with status={status}."
    out = []
    for r in recs:
        span = r.get("created", "?") + (f" → {r['ended']}" if r.get("ended") else " → …")
        out.append(
            f"#{r['id']} [{r['status'].upper()}] {r['project']} ({r['lang']}, {r['org']})\n"
            f"  {r['title']}\n"
            f"  +{r.get('additions', 0)}/-{r.get('deletions', 0)} · {r.get('files', 0)} files · {span}"
            f" · reviewer: {r.get('reviewer') or '—'}\n"
            f"  recap: {r.get('recap') or '⚠ none — run set_recap'}\n"
            f"  {r['url']}"
        )
    return "\n\n".join(out)


@mcp.tool()
def refresh(id: int = 0) -> str:
    """Pull live status, latest comment, and diff size from GitHub into the tracker.
    Pass a PR id to refresh one, or 0 (default) to refresh every contribution.
    Writes data/contributions.json. Flags any record whose recap is empty."""
    d = load()
    targets = [r for r in d["contributions"] if (id == 0 or r["id"] == id)]
    if not targets:
        return f"No contribution with id={id}."
    notes = []
    for r in targets:
        try:
            notes.append(_refresh_one(r))
        except Exception as e:  # keep going; report per-PR
            notes.append(f"#{r['id']} {r['project']}: ERROR {e}")
    save(d, updated_by="mcp")
    return "Refreshed from GitHub:\n" + "\n".join(notes) + \
        "\n\n(Run `sync` to push the update live.)"


@mcp.tool()
def pr_context(id: int) -> str:
    """Fetch a PR's title, body, changed files, and diff size so you can write or
    update its recap. Follow with set_recap(id, ...)."""
    d = load()
    rec = next((r for r in d["contributions"] if r["id"] == id), None)
    if not rec:
        return f"No contribution with id={id}."
    v = gh_json(["pr", "view", str(id), "--repo", rec["repo"], "--json",
                 "title,body,additions,deletions,changedFiles,files"])
    files = ", ".join(f.get("path", "") for f in (v.get("files") or [])[:12])
    return (f"#{id} {rec['repo']} — {v.get('title')}\n"
            f"+{v.get('additions')}/-{v.get('deletions')} · {v.get('changedFiles')} files\n"
            f"files: {files}\n\n"
            f"current recap: {rec.get('recap') or '(none)'}\n\n"
            f"--- PR body ---\n{(v.get('body') or '').strip()[:1600]}")


@mcp.tool()
def set_recap(id: int, recap: str) -> str:
    """Store the human-readable 'what this contribution does' recap for a PR."""
    d = load()
    rec = next((r for r in d["contributions"] if r["id"] == id), None)
    if not rec:
        return f"No contribution with id={id}."
    rec["recap"] = recap.strip()
    save(d, updated_by="mcp")
    return f"Recap saved for #{id} {rec['project']}. (Run `sync` to push.)"


@mcp.tool()
def list_potential(kind: str = "all") -> str:
    """List work to pick up. kind: all | prs (ready) | issues (watchlist)."""
    d = load()
    blocks = []
    if kind in ("all", "prs"):
        for p in d.get("potential_prs", []):
            blocks.append(f"[READY] {p['repo']}#{p['issue']} ({p['lang']}, {p['org']})\n"
                          f"  {p['title']}\n  why: {p.get('why', '')}\n  {p['url']}")
    if kind in ("all", "issues"):
        for p in d.get("potential_issues", []):
            blocks.append(f"[WATCH] {p['repo']}#{p['issue']} ({p['lang']}, {p['org']})\n"
                          f"  {p['title']}\n  why: {p.get('why', '')}\n  {p['url']}")
    return "\n\n".join(blocks) if blocks else "No potential work listed."


@mcp.tool()
def add_potential_pr(repo: str, issue: int, title: str, lang: str = "",
                     org: str = "", why: str = "", type: str = "",
                     labels: list[str] | None = None) -> str:
    """Add a ready-to-pick-up issue to the potential-PRs list.
    type: bug | docs | feature | enhancement | test | fix | chore."""
    d = load()
    d.setdefault("potential_prs", []).append({
        "repo": repo, "issue": issue, "lang": lang, "org": org,
        "url": f"https://github.com/{repo}/issues/{issue}",
        "title": title, "readiness": "ready", "type": type,
        "labels": labels or [], "why": why, "source": "added",
    })
    save(d, updated_by="mcp")
    return f"Added potential PR {repo}#{issue}. (Run `sync` to push.)"


@mcp.tool()
def add_potential_issue(repo: str, issue: int, title: str, lang: str = "",
                        org: str = "", why: str = "", type: str = "",
                        labels: list[str] | None = None) -> str:
    """Add an issue to the watchlist (needs vetting before picking up).
    type: bug | docs | feature | enhancement | test | fix | chore."""
    d = load()
    d.setdefault("potential_issues", []).append({
        "repo": repo, "issue": issue, "lang": lang, "org": org,
        "url": f"https://github.com/{repo}/issues/{issue}",
        "title": title, "readiness": "watch", "type": type,
        "labels": labels or [], "why": why, "source": "added",
    })
    save(d, updated_by="mcp")
    return f"Added watchlist issue {repo}#{issue}. (Run `sync` to push.)"


@mcp.tool()
def find_issues(repo: str, query: str = "", limit: int = 10) -> str:
    """Live-search open, unassigned issues in a repo to curate potential work from.
    Does not write anything — review results, then add_potential_pr/issue the good ones."""
    args = ["search", "issues", "--repo", repo, "--state", "open", "--no-assignee",
            "--limit", str(limit), "--json", "number,title,labels,updatedAt"]
    if query:
        args += ["--match", "title", query]
    rows = gh_json(args)
    if not rows:
        return f"No open unassigned issues found in {repo}" + (f" matching '{query}'." if query else ".")
    out = []
    for r in rows:
        labels = ", ".join(l.get("name", "") for l in (r.get("labels") or []))
        out.append(f"#{r['number']} {r['title']}\n  labels: {labels or '—'} · updated {(_date(r.get('updatedAt')))}")
    return f"Open unassigned issues in {repo}:\n\n" + "\n".join(out)


@mcp.tool()
def sync(message: str = "chore: refresh contributions") -> str:
    """git add + commit + push the repo so refreshed data and the dashboard go live."""
    steps = [
        ["git", "add", "-A"],
        ["git", "commit", "-m", message],
        ["git", "push"],
    ]
    log = []
    for s in steps:
        r = subprocess.run(s, cwd=ROOT, capture_output=True, text=True, timeout=60)
        log.append(f"$ {' '.join(s)}\n{(r.stdout or r.stderr).strip()}")
        if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
            return "Sync stopped:\n" + "\n".join(log)
    return "Synced:\n" + "\n".join(log)


@mcp.tool()
def people() -> str:
    """List the maintainers who reviewed your work — who they are, where they work,
    the strategic connection, and which PRs they touched."""
    d = load()
    ppl = d.get("people", {})
    if not ppl:
        return "No reviewers tracked yet. Run refresh_people."
    out = []
    for login, p in ppl.items():
        prs = ", ".join(f"#{n}" for n in p.get("reviewed", [])) or "—"
        out.append(f"{p.get('name', login)}  (@{login}) — {p.get('company') or '—'}"
                   f"{', ' + p['location'] if p.get('location') else ''}\n"
                   f"  reviewed: {prs} · {p.get('followers', 0)} followers · {p.get('url')}\n"
                   f"  connection: {p.get('connection', '')}")
    return "\n\n".join(out)


@mcp.tool()
def reviewer(login: str) -> str:
    """Pull live GitHub profile data for one reviewer and show the stored connection +
    which of your PRs they reviewed."""
    d = load()
    stored = d.get("people", {}).get(login, {})
    try:
        u = gh_json(["api", f"users/{login}"])
    except Exception as e:
        return f"Couldn't fetch @{login}: {e}"
    prs = ", ".join(f"#{n}" for n in stored.get("reviewed", [])) or "—"
    return (f"{u.get('name') or login}  (@{login})\n"
            f"  company:  {u.get('company') or '—'}\n"
            f"  location: {u.get('location') or '—'}\n"
            f"  bio:      {u.get('bio') or '—'}\n"
            f"  blog:     {u.get('blog') or '—'}\n"
            f"  stats:    {u.get('followers', 0)} followers · {u.get('public_repos', 0)} repos\n"
            f"  profile:  {u.get('html_url')}\n"
            f"  reviewed your: {prs}\n"
            f"  connection: {stored.get('connection', '(none stored — add one in data/contributions.json)')}")


@mcp.tool()
def refresh_people() -> str:
    """Refresh every reviewer's GitHub profile (name/company/location/blog/followers) and
    keep the reviewed-PRs list current. Preserves the human-written `connection` note."""
    d = load()
    reviewed = {}
    for c in d["contributions"]:
        r = c.get("reviewer")
        if r:
            reviewed.setdefault(r, []).append(c["id"])
    ppl = d.setdefault("people", {})
    notes = []
    for login, prs in reviewed.items():
        entry = ppl.setdefault(login, {"login": login, "connection": ""})
        try:
            u = gh_json(["api", f"users/{login}"])
            entry.update({
                "name": u.get("name") or login,
                "company": u.get("company") or "",
                "location": u.get("location"),
                "blog": u.get("blog") or "",
                "followers": u.get("followers", 0),
                "url": u.get("html_url"),
            })
            entry["reviewed"] = prs
            notes.append(f"@{login}: {entry['name']} · {entry['company'] or '—'}"
                         + ("" if entry.get("connection") else "  ⚠ no connection note"))
        except Exception as e:
            notes.append(f"@{login}: ERROR {e}")
    save(d, updated_by="mcp")
    return "Refreshed reviewers:\n" + "\n".join(notes) + "\n\n(Run `sync` to push.)"


# ------------------------------------------------------------------ radar tools
def load_roles() -> dict:
    return json.loads(ROLES.read_text())


def save_roles(data: dict, updated_by: str = "mcp") -> None:
    data.setdefault("meta", {})
    data["meta"]["updated_by"] = updated_by
    data["meta"]["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ROLES.write_text(json.dumps(data, indent=2) + "\n")


def load_cache() -> dict:
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def _find_role(data: dict, id: str) -> dict | None:
    return next((r for r in data["roles"] if r["id"] == id), None)


def _line(r: dict) -> str:
    score = r["tracks"].get(r["best_track"], 0)
    flag = "" if r["application"]["status"] == "none" else f"  [{r['application']['status'].upper()}]"
    return (f"{score:>3} {r['best_track']:<4} {r['tier']:<7} {r['company']} — {r['title']}{flag}\n"
            f"      {r.get('location') or '—'} · {r.get('season') or 'season n/a'} · "
            f"found {r.get('found')} · {r['url'] or 'no link'}")


@mcp.tool()
def radar_today() -> str:
    """Roles the radar first saw on its latest harvest, highest score first.
    This is the daily read: what opened, and which resume fits it."""
    data = load_roles()
    if not data["roles"]:
        return "No roles yet. Run find_roles."
    latest = max(r.get("found") or "" for r in data["roles"])
    new = [r for r in data["roles"]
           if r.get("found") == latest and r["application"]["status"] == "none"]
    new.sort(key=lambda r: -r["tracks"].get(r["best_track"], 0))
    if not new:
        return f"Nothing new on {latest}."
    head = f"{len(new)} new role(s) found {latest}:"
    return head + "\n\n" + "\n".join(_line(r) for r in new[:40])


@mcp.tool()
def list_roles(track: str = "all", tier: str = "all", status: str = "all",
               limit: int = 40) -> str:
    """Filter the board. track: all|swe|ml|hwv. tier: all|strong|fit|stretch.
    status: all|none|applied|in_progress|rejected|offer ('none' = not applied yet)."""
    rows = load_roles()["roles"]
    if track != "all":
        rows = [r for r in rows if r["best_track"] == track or track in (r.get("also_tracks") or [])]
    if tier != "all":
        rows = [r for r in rows if r["tier"] == tier]
    if status != "all":
        rows = [r for r in rows if r["application"]["status"] == status]
    if not rows:
        return f"No roles match track={track} tier={tier} status={status}."
    rows.sort(key=lambda r: -r["tracks"].get(r["best_track"], 0))
    more = f"\n\n(+{len(rows) - limit} more)" if len(rows) > limit else ""
    return f"{len(rows)} role(s):\n\n" + "\n".join(_line(r) for r in rows[:limit]) + more


@mcp.tool()
def find_roles(scope: str = "priority", ats: str = "") -> str:
    """Poll the ATS boards, keep PAID internships and co-ops in the US,
    de-duplicate, score, and append what is new.

    scope: priority   33 fast boards + 41 curated Workday employers  (~5 min)
           all        every greenhouse/ashby/lever/smartrecruiters   (~7 min)
           workday    all 1,710 Workday tenants                      (~25 min)
           everything both
    ats:   greenhouse | ashby | lever | smartrecruiters | workday"""
    data = load_roles()
    harvested, stats = radar.harvest(scope=scope, ats=ats or None)
    added, updated = radar.merge(data["roles"], harvested)
    cache = load_cache()
    radar.attach_descriptions(data["roles"], cache)
    for role in data["roles"]:
        radar.score_role(role, data["resumes"])
    data["roles"], dropped = radar.prune(data["roles"])
    cache.update(radar.split_descriptions(data["roles"]))
    CACHE.write_text(json.dumps(cache) + "\n")
    save_roles(data)
    tiers: dict[str, int] = {}
    for r in data["roles"]:
        tiers[r["tier"]] = tiers.get(r["tier"], 0) + 1
    return (f"Harvest ({scope}): {stats['ok']}/{stats['boards']} boards ok, "
            f"{stats['failed']} failed.\n"
            f"{stats['seen']} postings seen, {stats['kept']} internships/co-ops kept.\n"
            f"+{added} new roles, {updated} already known, {dropped} pruned. "
            f"Total {len(data['roles'])}.\n"
            f"Tiers: {tiers}\n\n(Run `sync` to push.)")


@mcp.tool()
def score_roles(id: str = "") -> str:
    """Re-run the relevancy formula. Pass a role id, or leave empty for all.
    Use after you edit the keyword lists in data/roles.json."""
    data = load_roles()
    radar.attach_descriptions(data["roles"], load_cache())
    targets = [r for r in data["roles"] if not id or r["id"] == id]
    if not targets:
        return f"No role with id={id}."
    before = {r["id"]: r["tier"] for r in targets}
    for r in targets:
        radar.score_role(r, data["resumes"])
    moved = [r for r in targets if before[r["id"]] != r["tier"]]
    radar.split_descriptions(data["roles"])
    save_roles(data)
    out = f"Scored {len(targets)} role(s). {len(moved)} changed tier."
    if moved:
        out += "\n" + "\n".join(f"  {r['company']} — {r['title']}: "
                                f"{before[r['id']]} → {r['tier']}" for r in moved[:20])
    return out + "\n\n(Run `sync` to push.)"


@mcp.tool()
def set_why(id: str, why: str) -> str:
    """Replace a role's machine-drafted reason with a human sentence.
    A human `why` is never overwritten by score_roles."""
    data = load_roles()
    role = _find_role(data, id)
    if not role:
        return f"No role with id={id}."
    role["why"] = why.strip()
    role["why_by"] = "human"
    save_roles(data)
    return f"Why saved for {role['company']} — {role['title']}. (Run `sync` to push.)"


@mcp.tool()
def mark_applied(id: str, resume: str = "", date: str = "", notes: str = "") -> str:
    """Record that you applied. resume: swe|ml|hwv (defaults to the best track).
    date: YYYY-MM-DD (defaults to today)."""
    data = load_roles()
    role = _find_role(data, id)
    if not role:
        return f"No role with id={id}."
    valid = [r["id"] for r in data["resumes"]]
    resume = resume or role["best_track"]
    if resume not in valid:
        return f"resume must be one of {valid}."
    role["application"].update({
        "status": "applied", "applied": date or radar.today(), "resume": resume,
        "notes": notes or role["application"].get("notes", ""),
    })
    save_roles(data)
    return (f"Marked applied: {role['company']} — {role['title']} "
            f"({resume} resume, {role['application']['applied']}).\n"
            f"Add it to the spreadsheet with sheet_push.")


@mcp.tool()
def set_status(id: str, status: str, notes: str = "") -> str:
    """Move an application along. status: none|applied|in_progress|phone_screen|
    rejected|offer. These match the spreadsheet's Status column exactly."""
    allowed = ("none", "applied", "in_progress", "phone_screen", "rejected", "offer")
    if status not in allowed:
        return f"status must be one of {allowed}."
    data = load_roles()
    role = _find_role(data, id)
    if not role:
        return f"No role with id={id}."
    was = role["application"]["status"]
    role["application"]["status"] = status
    if notes:
        role["application"]["notes"] = notes
    save_roles(data)
    return f"{role['company']} — {role['title']}: {was} → {status}. (Run `sync` to push.)"


@mcp.tool()
def sheet_push(limit: int = 40) -> str:
    """Print the rows to paste into the application spreadsheet: every role marked
    applied in the radar that carries no sheet_row yet. Tab-separated, in the
    sheet's own column order, so it pastes straight into the next empty row.

    It prints rather than writes: the Sheets API is not wired up, and an append
    that cannot be undone should not happen behind your back.
    """
    data = load_roles()
    pending = [r for r in data["roles"]
               if r["application"]["status"] != "none" and not r["application"].get("sheet_row")]
    if not pending:
        return "Nothing to push — every applied role already has a sheet row."
    pending.sort(key=lambda r: str(r["application"].get("applied") or ""))
    cols = ["Company", "Role", "Location", "App Link", "Recruiter", "Date Found",
            "Network Connections?", "Date Applied", "App Attachments", "Status",
            "Thank You Note Sent?", "Follow-Up Sent?", "Comments/Notes"]
    lines = ["\t".join(cols)]
    for r in pending[:limit]:
        a = r["application"]
        resume = next((x["label"] for x in data["resumes"] if x["id"] == a.get("resume")), "")
        lines.append("\t".join([
            r["company"], r["title"], r.get("location") or "", r.get("url") or "",
            a.get("recruiter") or "", _mmdd(r.get("found")), a.get("network") or "",
            _mmdd(a.get("applied")), f"Resume ({resume})" if resume else "",
            a["status"].replace("_", " ").title(),
            "Yes" if a.get("thank_you") else "", "Yes" if a.get("follow_up") else "",
            a.get("notes") or "",
        ]))
    return (f"{len(pending)} row(s) to paste into the spreadsheet:\n\n" + "\n".join(lines)
            + "\n\nAfter you paste, run sheet_mark(id, row) so the radar stops offering them.")


def _mmdd(iso: str | None) -> str:
    """ISO date back to the sheet's own MM/DD format."""
    return f"{iso[5:7]}/{iso[8:10]}" if iso and len(iso) >= 10 else ""


@mcp.tool()
def sheet_mark(id: str, row: int) -> str:
    """Record which spreadsheet row a role lives in, after you paste it."""
    data = load_roles()
    role = _find_role(data, id)
    if not role:
        return f"No role with id={id}."
    role["application"]["sheet_row"] = row
    save_roles(data)
    return f"{role['company']} — {role['title']} is sheet row {row}."


@mcp.tool()
def needs_human() -> str:
    """Every row the sync could not resolve on its own: a spreadsheet row with no
    role title, or a role flagged for a decision. These block a clean sheet sync."""
    rows = load_roles()["roles"]
    flagged = [r for r in rows if str(r["application"].get("notes", "")).startswith("needs_human")]
    untitled = [r for r in rows if r["source"] == "sheet" and "role not recorded" in r["title"]]
    out = []
    if untitled:
        out.append(f"{len(untitled)} spreadsheet row(s) with no role title — "
                   f"the radar cannot tell which posting it was:")
        out += [f"  row {r['application']['sheet_row']}: {r['company']} "
                f"(applied {r['application'].get('applied') or '?'})" for r in untitled]
    other = [r for r in flagged if r not in untitled]
    if other:
        out.append(f"\n{len(other)} role(s) flagged:")
        out += [f"  {r['company']} — {r['title']}: {r['application']['notes']}" for r in other]
    return "\n".join(out) if out else "Nothing needs a human right now."


@mcp.tool()
def radar_summary() -> str:
    """Counts by tier, track, and application status, plus what is worth a look."""
    data = load_roles()
    rows = data["roles"]
    tiers, tracks, states = {}, {}, {}
    for r in rows:
        tiers[r["tier"]] = tiers.get(r["tier"], 0) + 1
        tracks[r["best_track"]] = tracks.get(r["best_track"], 0) + 1
        states[r["application"]["status"]] = states.get(r["application"]["status"], 0) + 1
    open_strong = [r for r in rows if r["tier"] == "strong"
                   and r["application"]["status"] == "none" and not r["dead"]]
    open_strong.sort(key=lambda r: -r["tracks"][r["best_track"]])
    lines = [
        f"Internship radar — {data['meta'].get('owner')}",
        f"Roles: {len(rows)}  |  tiers: {tiers}",
        f"Best track: {tracks}",
        f"Applications: {states}",
        f"Last write: {data['meta'].get('generated')} by {data['meta'].get('updated_by')}",
    ]
    if open_strong:
        lines.append(f"\nStrong fits not yet applied to ({len(open_strong)}):")
        lines += ["  " + _line(r).split("\n")[0] for r in open_strong[:12]]
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()

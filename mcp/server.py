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
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "contributions.json"

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


if __name__ == "__main__":
    mcp.run()

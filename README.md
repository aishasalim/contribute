# contributie

Two static pages over two JSON files, kept current by one **MCP server** (`mcp/server.py`).
The MCP writes the JSON; the pages render it. The JSON schema is the seam, so **Hermes** can
take over the writes later without a change to either page.

| Page | File | Data | What it answers |
|------|------|------|-----------------|
| **Contributions** | `index.html` | `data/contributions.json` | What open-source work did I ship, and what do I pick up next? |
| **Internship radar** | `radar.html` | `data/roles.json` | What roles opened today, which of my three resumes fits, and did I apply? |

Both pages share the sidebar shell, the theme toggle, and the master–detail layout. The
sidebar carries a page switch at the top, so the two pages read as one product.

Live: <https://aishasalim.github.io/contributie/> · <https://contribute-drab.vercel.app>

---

## Page 1 — Contributions

A tracker for open-source contributions: merged PRs, PRs in review, closed ones, and vetted
leads to pick up. Each record carries a plain-language recap of what the change actually does.

Sections: **In review** · **Merged** · **Potential PRs** · **Watchlist** · **Closed**.

`refresh` only touches machine facts (status, comments, diff size, dates). Recaps stay
human-written through `set_recap`, so they read like a person wrote them.

## Page 2 — Internship radar

A radar for internship roles, ranked against three resumes. Full specification:
**[docs/RADAR.md](docs/RADAR.md)**.

Three resume tracks. Every role gets a score per track, and the best track becomes its badge.

| Track | Badge | Resume | Matches on |
|-------|-------|--------|-----------|
| `swe` | **SWE** | Software engineering | backend, full stack, platform, infrastructure, distributed systems |
| `ml` | **ML** | Machine learning / AI | ML engineer, AI platform, research, LLM, data science, MLOps |
| `hwv` | **HW VERIF** | Hardware verification | RTL, SystemVerilog, UVM, DV, FPGA, ASIC, silicon, EDA, firmware |

Sections: **Today** · **Strong fit** · **All open** · **Applied** · **Closed**.

- **Today** lists the roles the radar first saw today, highest score first. This is the daily read.
- **Applied** is the running list. It mirrors the
  [application spreadsheet](https://docs.google.com/spreadsheets/d/1afc67q-MdqMuV5lhJqVRs1X0EbclHwM9iTT29g5-hro/edit),
  which stays the source of truth for application state.
- Relevancy is a transparent formula, not a black box. Every score ships with a one-line
  reason. See [Relevancy model](docs/RADAR.md#relevancy-model).

---

## MCP server

Python + [FastMCP]. It shells out to the `gh` CLI (already authenticated), so there is no
token to configure. Radar tools also read public ATS endpoints and the Google Sheet.

### Contribution tools

| Tool | What it does |
|------|--------------|
| `summary()` | Counts by status + what needs attention next |
| `list_contributions(status)` | `all\|active\|merged\|review\|awaiting\|closed` |
| `refresh(id=0)` | Pull live status / latest comment / diff from GitHub (0 = all) |
| `pr_context(id)` | PR title/body/diff/files, so you can write a recap |
| `set_recap(id, recap)` | Store the human-readable "what this does" recap |
| `list_potential(kind)` | `all\|prs\|issues` |
| `add_potential_pr(...)` / `add_potential_issue(...)` | Add a lead |
| `find_issues(repo, query, limit)` | Live-search open, unassigned issues to curate from |
| `people()` / `reviewer(login)` / `refresh_people()` | Maintainers who reviewed your work |

### Radar tools

| Tool | What it does |
|------|--------------|
| `radar_today()` | Roles first seen today, highest score first |
| `list_roles(track, tier, status)` | Filter the board |
| `find_roles(source, limit)` | Poll ATS feeds, de-duplicate, append new roles |
| `score_roles(id=0)` | Re-run the relevancy formula (0 = all) |
| `set_why(id, why)` | Store the human-written one-line reason |
| `mark_applied(id, resume, date)` | Flip status and stamp which resume you sent |
| `sheet_pull()` | Read the spreadsheet; mirror application state into `roles.json` |
| `sheet_push()` | Append newly applied roles back to the spreadsheet |

### Shared

| Tool | What it does |
|------|--------------|
| `sync(message)` | git add + commit + push, so both pages go live |

`set_why` follows the same rule as `set_recap`: the machine scores, the human explains.

### Register in Claude Code

No install step — `uv` fetches `mcp` on the fly. From the repo root:

```bash
claude mcp add contributie -- uv run --with mcp --python 3.12 python <ABS_PATH>/contributie/mcp/server.py
```

or add to `.mcp.json`:

```json
{
  "mcpServers": {
    "contributie": {
      "command": "uv",
      "args": ["run", "--with", "mcp", "--python", "3.12", "python",
               "<ABS_PATH>/contributie/mcp/server.py"]
    }
  }
}
```

Restart Claude Code, then: *"refresh my contributions and sync"*, or *"what opened on the
radar today?"*

## Typical loops

**Contributions**

1. `refresh` → pull the latest status and comments from GitHub.
2. If a recap is flagged empty: `pr_context(id)` → read it → `set_recap(id, "...")`.
3. `find_issues(repo)` → curate → `add_potential_pr` / `add_potential_issue`.
4. `sync` → push; the dashboard updates.

**Radar**

1. `find_roles()` → poll the ATS feeds and append what is new.
2. `score_roles()` → rank every open role against the three resumes.
3. `radar_today()` → read the new strong fits.
4. Apply, then `mark_applied(id, resume)` → `sheet_push()`.
5. `sheet_pull()` → pull back status changes you made in the spreadsheet by hand.
6. `sync` → push.

## Data

Two files, one schema convention. Each has a `meta` block with `owner`, `generated`,
`updated_by` (`mcp` | `hermes` | `manual`), and `schema`.

`data/contributions.json`:

```
meta               owner, focus, generated, updated_by, schema
contributions[]    id, repo, project, lang, org, kind, url, title,
                   status (merged|review|awaiting|closed), created, ended,
                   additions, deletions, files, reviewer, review_decision,
                   recap, latest_comment{author,at,body}, next_action, blocked_on
potential_prs[]    repo, issue, lang, org, url, title, readiness:"ready", why, source
potential_issues[] same shape, readiness:"watch"
people{}           login -> name, company, location, followers, url, reviewed[], connection
```

`data/roles.json`:

```
meta               owner, seasons[], generated, updated_by, schema, sources[]
resumes[]          id (swe|ml|hwv), label, file, titles[], keywords[], color
roles[]            id, company, title, location, workmode, season, url, source,
                   posted, found, eligibility{}, tags[],
                   tracks{swe,ml,hwv}, best_track, tier, why,
                   application{status, applied, resume, sheet_row, recruiter,
                               network, thank_you, follow_up, notes},
                   dead
```

Field-by-field definitions, the score formula, and the spreadsheet column map live in
[docs/RADAR.md](docs/RADAR.md).

## Hermes seam

Any writer that preserves these schemas can own the updates. That is how Hermes replaces the
MCP without a change to either page.

For the radar, Hermes will also apply on your behalf. The guardrails for that are a contract,
not a preference — see [Auto-apply contract](docs/RADAR.md#auto-apply-contract).

## Hosting

Static, no build step. Serve over http; a `file://` path fails because browsers block the
`fetch`.

- **GitHub Pages** — Settings → Pages → deploy from `main` / root.
- **Vercel** — static, root directory.
- **Azure Static Web Apps** — `staticwebapp.config.json`. `radar.html` is a real file, so the
  navigation fallback does not touch it.

[FastMCP]: https://github.com/modelcontextprotocol/python-sdk

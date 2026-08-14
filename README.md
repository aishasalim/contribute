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

**Paid internships and co-ops only**, ranked against three resumes. New grad, early career
and campus full-time titles are filtered out, and so is anything the posting calls unpaid,
volunteer, or credit-only. Where a pay range is stated it is extracted and shown. Full specification: **[docs/RADAR.md](docs/RADAR.md)**.

Three resume tracks. Every role gets a score per track, and the best track becomes its badge.

| Track | Badge | Resume | Matches on |
|-------|-------|--------|-----------|
| `swe` | **SWE** | Software engineering | backend, full stack, platform, infrastructure, distributed systems |
| `ml` | **ML** | Machine learning / AI | ML engineer, AI platform, research, LLM, data science, MLOps |
| `hwv` | **HW VERIF** | Hardware verification | RTL, SystemVerilog, UVM, DV, FPGA, ASIC, silicon, EDA, firmware |

Sections: **Today** · **Strong fit** · **All open** · **Applied** · **Closed**.

- **Today** lists the roles the latest harvest first saw, highest score first. The daily read.
- **Applied** is the running list. It mirrors the
  [application spreadsheet](https://docs.google.com/spreadsheets/d/1afc67q-MdqMuV5lhJqVRs1X0EbclHwM9iTT29g5-hro/edit),
  which stays the source of truth for application state.
- Every row shows **how many days ago the posting went up**, computed in the browser so it
  stays correct between harvests. Sort by best fit or by newest.
- Relevancy is a transparent formula, not a black box. Every score ships with a one-line
  reason. See [Relevancy model](docs/RADAR.md#relevancy-model).

The page reads a static JSON file. It is **not** live — it changes when `find_roles` runs and
the file is pushed.

## Backend (optional)

The board needs none. `radar.html` reads `data/roles.json` and that is the whole product.

A backend matters only when **Hermes** writes back: an agent cannot share a JSON file in git
with a dashboard. The recommended target is **Supabase** — free, and it is Postgres *plus* an
auto-generated REST API, so there is no server to deploy.

```
db/schema.sql     tables, indexes, status-change trigger, the `radar` view
db/supabase.sql   row level security, the `hermes_queue` view, the four write functions
db/sync.py        push / pull / stats between roles.json and Postgres
api/main.py       a self-hosted FastAPI equivalent, if you would rather not use Supabase
```

`roles` is a cache the harvest overwrites. `applications` is your own state, and a harvest
never touches it. Step-by-step: **[docs/SETUP.md](docs/SETUP.md)**. The agent loop:
**[docs/HERMES.md](docs/HERMES.md)**.

---

## MCP server

Python + [FastMCP]. It shells out to the `gh` CLI (already authenticated), so there is no
token to configure. Radar tools read public ATS job-board endpoints over stdlib urllib —
no extra dependency, no browser.

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
| `radar_summary()` | Counts by tier, track, status + the strong fits you have not applied to |
| `radar_today()` | Roles the latest harvest found, highest score first |
| `list_roles(track, tier, status)` | Filter the board |
| `find_roles(scope, ats)` | Poll ATS boards, de-duplicate, score, append. `scope`: `priority` (33 boards, ~15 s) or `all` (2,272 boards, ~7 min) |
| `score_roles(id)` | Re-run the relevancy formula (empty = all) |
| `set_why(id, why)` | Replace the machine reason with a human sentence |
| `mark_applied(id, resume, date)` | Flip status and stamp which resume you sent |
| `set_status(id, status)` | Move an application along |
| `sheet_push(limit)` | Print the tab-separated rows to paste into the spreadsheet |
| `sheet_mark(id, row)` | Record which spreadsheet row a role lives in |
| `needs_human()` | Rows the sync cannot resolve alone |

`sheet_push` **prints** rows rather than writing them. The Sheets API is not wired up, and an
append that cannot be undone should not happen behind your back. Paste the rows, then call
`sheet_mark`.

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

1. `find_roles()` → poll the 33 priority boards and append what is new. Once a week,
   `find_roles(scope="all")` for the full 2,272-board sweep.
2. `radar_today()` → read what opened, highest score first.
3. Apply, then `mark_applied(id, resume)`.
4. `sheet_push()` → paste the printed rows into the spreadsheet → `sheet_mark(id, row)`.
5. `sync` → push.

`find_roles` scores as it goes, so `score_roles` is only needed after you edit a keyword list.

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
                   posted, found, snippet, eligibility{}, tags[],
                   tracks{swe,ml,hwv}, best_track, also_tracks[], tier, why, why_by,
                   application{status, applied, resume, sheet_row, recruiter,
                               network, thank_you, follow_up, notes},
                   dead
```

`data/boards.json` holds the 2,272-row company-to-ATS registry, vendored from an
MIT-licensed upstream list — see [NOTICE](NOTICE). `data/blocklist.json` drops junk at
harvest time (unpaid "volunteer intern" listings, blocked companies). Full job descriptions are stripped out
of `roles.json` and cached in `data/descriptions.cache.json`, which is git-ignored: they run
5–20 KB each and would make the file too heavy for the page to fetch.

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

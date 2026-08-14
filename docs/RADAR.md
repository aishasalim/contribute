# Internship radar — specification

The radar is page 2 of contributie (`radar.html`, backed by `data/roles.json`). It covers
**internships and co-ops only** — new grad, early career and campus full-time titles are
filtered out. It answers three questions in order:

1. **What opened today?**
2. **Which of my three resumes fits it?**
3. **Did I apply, and what happened?**

The spreadsheet stays the source of truth for what happened after you applied. The radar is
the source of truth for discovery and relevancy. [Sync rules](#spreadsheet-map) below define
which side wins for each field.

---

## Resume tracks

Three resumes, three tracks. A role is scored against all three. The highest score becomes
the role's badge. A second badge appears when another track lands within 10 points, because
some roles genuinely fit two resumes.

| Track | Badge | Resume | Signal words |
|-------|-------|--------|--------------|
| `swe` | **SWE** | Software engineering | software engineer, backend, full stack, platform, infrastructure, distributed systems, API, Go, Java, TypeScript, React |
| `ml` | **ML** | Machine learning / AI | machine learning, AI, LLM, research, data science, MLOps, PyTorch, inference, model, NLP, computer vision |
| `hwv` | **HW VERIF** | Hardware verification | verification, RTL, SystemVerilog, UVM, DV, FPGA, ASIC, silicon, EDA, firmware, timing, synthesis, testbench, cocotb |

Tracks live in `roles.json` under `resumes[]`, not in the page. Edit the keyword lists there
and re-run `score_roles()`; the page needs no change.

```json
{
  "id": "hwv",
  "label": "HW Verif",
  "file": "resumes/aisha-hardware-verification.pdf",
  "titles": ["design verification", "verification engineer", "silicon", "fpga", "asic"],
  "keywords": ["systemverilog", "uvm", "rtl", "testbench", "cocotb", "eda", "synthesis"],
  "color": "#12b5a5"
}
```

The `file` path is a label for the dashboard and a pointer for Hermes. Resume PDFs are **not**
committed to this repo.

## Relevancy model

The score is a weighted sum of five signals. Every signal is 0–1. The result is 0–100. The
formula is deliberately simple, so a bad rank is debuggable.

```
score(track) = 100 * ( 0.45 * title_match
                     + 0.25 * keyword_density
                     + 0.15 * seniority_fit
                     + 0.10 * eligibility
                     + 0.05 * freshness )
```

| Signal | 1.0 means | 0.0 means |
|--------|-----------|-----------|
| `title_match` | The job title contains a phrase from the track's `titles[]` | No title phrase appears |
| `keyword_density` | 6 or more distinct track keywords appear in the description | No keyword appears |
| `seniority_fit` | The posting says intern, co-op, or new grad | The posting requires 5+ years |
| `eligibility` | No sponsorship or citizenship bar you cannot clear | The posting requires clearance or citizenship you do not hold |
| `freshness` | Posted in the last 3 days | Posted more than 30 days ago |

`keyword_density` counts **distinct** keywords, not repeats, so a job description cannot
inflate its score by repeating one word.

### Tiers

| Tier | Score | Treatment |
|------|-------|-----------|
| `strong` | ≥ 75 | Shown in **Strong fit**. Hermes may auto-apply. |
| `fit` | 55–74 | Shown in **All open**. Apply by hand. |
| `stretch` | 35–54 | Shown behind a filter. |
| — | < 35 | Stored, hidden by default. |

Sub-35 roles stay in the file. They are the negative examples that show the keyword lists
need work.

### The `why` line

Every role carries one sentence that names the top two signals that produced its score.
`score_roles()` writes a machine draft and stamps `why_by: "auto"`. `set_why(id, why)` replaces
it with a human sentence and stamps `why_by: "human"`, which `score_roles` then never overwrites.
This mirrors `set_recap` on page 1: the machine ranks, the human explains.

> Title matches "Design Verification Intern" and the description names UVM and SystemVerilog;
> no sponsorship bar.

## Role schema

`data/roles.json`:

```json
{
  "meta": {
    "owner": "aishasalim",
    "seasons": ["summer-2027", "fall-2026", "winter-2027"],
    "generated": "2026-08-14T00:00:00Z",
    "updated_by": "mcp",
    "schema": 1,
    "sources": ["greenhouse", "lever", "ashby", "smartrecruiters", "workday", "manual"]
  },
  "resumes": [],
  "roles": []
}
```

One role:

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | Stable slug of `company + title + url`. Never renumbered, so the spreadsheet can point at it. |
| `company` | string | Employer name, as the ATS reports it |
| `title` | string | Job title, unedited |
| `location` | string | Free text; multi-site postings keep every site |
| `workmode` | enum | `onsite` \| `hybrid` \| `remote` \| `unspecified` |
| `season` | enum | `summer-2027` \| `fall-2026` \| `winter-2027` \| `spring-2027` \| `null` when the posting does not say |
| `url` | string | Direct apply link, not an aggregator |
| `source` | enum | Which feed found it |
| `posted` | date | The employer's own published date |
| `found` | date | When the radar first saw it. **Today** filters on this. |
| `paid` | bool\|null | `false` only when the posting says unpaid. `null` means it is silent — most are. |
| `pay` | string | The extracted range, e.g. `$45.00 – $60.00 / hour`. Empty when not stated. |
| `eligibility` | object | `{ sponsorship: bool\|null, citizenship: string\|null, class_year: [] }`. `null` means the posting is silent. |
| `tags[]` | string[] | Skills detected in the description (Python, C++, UVM, React) |
| `tracks` | object | `{ swe: 0-100, ml: 0-100, hwv: 0-100 }` |
| `best_track` | enum | `swe` \| `ml` \| `hwv` |
| `tier` | enum | `strong` \| `fit` \| `stretch` |
| `why` | string | One sentence naming the top two signals |
| `why_by` | enum | `auto` \| `human`. A human `why` is never overwritten. |
| `also_tracks` | string[] | Other tracks within 10 points of the best |
| `snippet` | string | First 280 characters of the description, for the page |
| `application` | object | See below |
| `dead` | bool | The link 404s or the posting was pulled. Moves the role to **Closed**. |

`application`:

| Field | Type | Notes |
|-------|------|-------|
| `status` | enum | `none` \| `applied` \| `in_progress` \| `phone_screen` \| `rejected` \| `offer` |
| `applied` | date | ISO `YYYY-MM-DD` |
| `resume` | enum | Which resume you sent: `swe` \| `ml` \| `hwv` |
| `sheet_row` | int | Row number in the spreadsheet, or `null` |
| `recruiter` | string | Name and title |
| `network` | string | Who you know there |
| `thank_you` | bool | Thank-you note sent |
| `follow_up` | bool | Follow-up sent |
| `notes` | string | Free text |

The `status` values match the spreadsheet's Status column exactly, so the sync needs no
translation table.

## Page sections

| Section | Filter |
|---------|--------|
| **Today** | `found == today`, sorted by score, highest first |
| **Strong fit** | `tier == "strong"` and `application.status == "none"` and `dead == false` |
| **All open** | `dead == false`, grouped by tier |
| **Applied** | `application.status != "none"`, sorted by `applied`, newest first |
| **Closed** | `application.status == "rejected"` or `dead == true` |

Each row shows the score, the company, the title, the track badge, and the **posting age in
days**. Age is computed in the browser from `posted` (falling back to `found`), so it stays
correct without a re-harvest. Anything 3 days old or newer is highlighted; anything past 30
days is dimmed. A sort toggle switches between **best fit** and **newest**.

The detail rail shows the track scores as three bars, the `why` line, the pay, the
eligibility flags, the skill tags, the application record, and one **Apply** link.

## Spreadsheet map

The [application spreadsheet](https://docs.google.com/spreadsheets/d/1afc67q-MdqMuV5lhJqVRs1X0EbclHwM9iTT29g5-hro/edit)
has two header bands. The band decides which side wins on a conflict.

| Sheet column | Band | JSON field | Source of truth |
|--------------|------|------------|-----------------|
| Company | Step 1 | `company` | Radar |
| Role | Step 1 | `title` | Radar |
| Location | Step 1 | `location` | Radar |
| App Link | Step 1 | `url` | Radar |
| Recruiter | Step 1 | `application.recruiter` | Sheet |
| Date Found | Step 1 | `found` | Radar |
| Network Connections? | Step 1 | `application.network` | Sheet |
| Date Applied | Step 2 | `application.applied` | Sheet |
| App Attachments | Step 2 | `application.resume` | Sheet |
| Status | Step 2 | `application.status` | Sheet |
| Thank You Note Sent? | Step 2 | `application.thank_you` | Sheet |
| Follow-Up Sent? | Step 2 | `application.follow_up` | Sheet |
| Comments/Notes | Step 2 | `application.notes` | Sheet |

Rule: **Step 1 columns flow radar → sheet. Step 2 columns flow sheet → radar.**

### Two known hazards

1. **The sheet stores dates as `MM/DD` with no year.** `sheet_pull()` must add the year. Use
   the row's neighbours to pick it, and never write a date in the future.
2. **Many rows have a Company but no Role.** Match those rows on company plus nearest
   `Date Applied`, then flag the row for a human. Do not guess which of three roles at one
   company it was.

`sheet_push()` appends only. It never edits or deletes a row you wrote by hand.

## Sourcing

Poll public ATS endpoints. They return JSON, they are stable, and they do not need a browser.

### The board registry

`data/boards.json` holds **2,272** company-to-board rows. It is vendored from an MIT-licensed
upstream list — see [NOTICE](../NOTICE) for the attribution. Rebuilding that list by hand
would take days, so the radar does not try.

| Source | Boards | Endpoint shape |
|--------|-------:|----------------|
| Greenhouse | 1,021 | `boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true` |
| Ashby | 651 | `api.ashbyhq.com/posting-api/job-board/{board}` |
| Lever | 358 | `api.lever.co/v0/postings/{board}?mode=json` |
| SmartRecruiters | 242 | `api.smartrecruiters.com/v1/companies/{board}/postings` |

Workday carries another 1,710 companies upstream, but each tenant needs its own CxS JSON POST
endpoint. It is not wired up. That is the largest single gap in coverage.

`find_roles` takes a scope:

| Scope | Boards | Time | Use |
|-------|-------:|------|-----|
| `priority` | 33 | ~15 s | The hand-verified set. The daily poll. |
| `all` | 2,272 | ~7 min | The weekly sweep. |

Boards are polled 12 at a time. A board that 404s is counted and skipped; it never stops the
sweep. About 117 of 2,272 fail on any given run, because slugs go stale upstream.

### Rules

- De-duplicate on `id` (company + title) across sources. The first `found` date wins and is
  never overwritten, so a role cannot resurface as new every week.
- Record the employer's `posted` date once. Do not refresh it, or every role looks new.
- Filter on the **title** for internship and co-op wording, never the description. A
  description filter let ~1,700 senior roles through, because full-time postings mention
  "students" and "university" in the boilerplate.
- **Paid only.** `pay_of()` reads the description and sets `paid` to `false` on `unpaid`,
  `volunteer`, `pro bono`, `no compensation`, or `credit only`. Those rows are dropped.
  `stipend` and `academic credit` are **not** unpaid signals on their own: a stipend is pay,
  and many paid internships also offer credit. A silent posting (`paid: null`) is kept —
  about half say nothing, and dropping them would empty the board.
- Where a range is stated, it is extracted into `pay` (`$45.00 – $60.00 / hour`). Roughly a
  quarter of postings state one.
- `data/blocklist.json` drops rows at harvest time: blocked company slugs, and title
  patterns for unpaid work (`volunteer`, `unpaid`, `pro bono`) and degrees you do not hold.
  Without it, one media company's 52 unpaid "intern/volunteer" listings put 9 rows into the
  fit tier. The convention comes from the reference internship-list projects.
- Descriptions are stripped out of `roles.json` before it is written, and cached in
  `data/descriptions.cache.json` (git-ignored). Several hundred descriptions at 5–20 KB each
  would make the file too heavy for the page to fetch. The page keeps a 280-character
  `snippet`; `score_roles` re-reads the cache.
- **LinkedIn: link out, do not scrape.** It blocks automated reads and the terms forbid it.
- Playwright is the last resort, for a board with no JSON at all. Run it locally, not in CI.
  It is not part of the current toolchain, so add it only when a target actually needs it.

## Database

The spreadsheet is not wired up. Postgres is the durable store instead —
`db/schema.sql` and `db/sync.py`, sized for DigitalOcean Managed Postgres.

Two tables, and the split is the point:

| Table | Owner | A harvest |
|-------|-------|-----------|
| `roles` | The ATS boards | overwrites it |
| `applications` | You | **never touches it** |

The spreadsheet mixed both, so a re-import risked overwriting an application you had already
sent. `applications` also carries a trigger that writes every status change to
`application_events`, so a rejection that arrives months later still has a date.

```bash
export DATABASE_URL='postgresql://user:pass@host:25060/db?sslmode=require'
uv run --with 'psycopg[binary]' python db/sync.py init    # create the schema
uv run --with 'psycopg[binary]' python db/sync.py push    # roles.json -> DB
uv run --with 'psycopg[binary]' python db/sync.py pull    # DB -> roles.json
uv run --with 'psycopg[binary]' python db/sync.py stats
```

`push` upserts roles and seeds an application row **only when none exists**. `pull` writes DB
application state back into `roles.json`, which the page reads.

The page still fetches a static `roles.json`; it does not query Postgres. A browser cannot
reach a managed database directly, so a live page needs a small read API in front of it.
That is not built.

## Auto-apply contract

Hermes will apply on your behalf. These are hard preconditions, not preferences. Hermes may
submit an application only when **every** line holds:

1. `tier == "strong"`
2. `application.status == "none"`
3. `dead == false`
4. `eligibility` shows no bar it cannot clear
5. The role is in a season listed in `meta.seasons`

Further rules:

- The resume it attaches is `best_track`. It never picks a different one.
- It answers **only** the fields it can fill from your profile: name, contact, school,
  graduation date, work authorisation, links.
- Any free-text question — "why this company", "describe a project" — stops the run. Hermes
  sets `application.status = "none"` and writes `needs_human` into `notes`. It never invents
  an answer in your voice.
- It never answers a demographic or self-identification question. It leaves those blank.
- On success it writes `status`, `applied`, `resume`, then calls `sheet_push()`.
- One company gets at most 3 auto-applications per day.

Every auto-application is logged with the timestamp, the role `id`, and the resume sent, so
the run is auditable after the fact.

## Open questions

- **Score calibration.** The weights are a first guess. Re-fit them once 50 roles have an
  outcome in the sheet, and check whether `strong` actually converts better than `fit`.
- **Company allowlist.** The reference projects poll thousands of boards. Start with the
  companies already in the sheet plus the hardware and EDA names on page 1, then grow.
- **Description storage.** Scoring needs the full description; the page does not. Decide
  whether to keep it in `roles.json` or a separate cache before the file gets large.

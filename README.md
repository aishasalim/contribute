# contributie

A tracker for open-source contributions: a live **dashboard** (`index.html`) backed by a
single JSON file (`data/contributions.json`), kept up to date by an **MCP server**
(`mcp/server.py`) that pulls status, comments, and diff size from GitHub.

- **History of merged PRs**, PRs **in review**, and closed ones — each with a plain-language
  recap of what it actually does.
- **Potential PRs to pick up** (vetted, ready) and **issues to watch** (need confirmation).
- The MCP updates everything now; the JSON schema is the seam so **Hermes** can take over later.

## Dashboard

`index.html` fetches `data/contributions.json` at runtime and renders it — merged history,
active PRs, potential work, closed. Serve it over http (GitHub Pages: Settings → Pages →
deploy from `main` / root). It won't load from a `file://` path because browsers block the
`fetch`.

## MCP server

Python + [FastMCP], shells out to the `gh` CLI (already authenticated — no token to set).

### Tools

| Tool | What it does |
|------|--------------|
| `summary()` | Counts by status + what needs attention next |
| `list_contributions(status)` | `all\|active\|merged\|review\|awaiting\|closed` |
| `refresh(id=0)` | Pull live status / latest comment / diff from GitHub (0 = all). Writes the JSON. |
| `pr_context(id)` | PR title/body/diff/files, so you can write a recap |
| `set_recap(id, recap)` | Store the human-readable "what this does" recap |
| `list_potential(kind)` | `all\|prs\|issues` |
| `add_potential_pr(...)` / `add_potential_issue(...)` | Add a lead |
| `find_issues(repo, query, limit)` | Live-search open, unassigned issues to curate from |
| `sync(message)` | git add + commit + push so data + dashboard go live |

`refresh` only touches machine facts (status, comments, diff, dates); **recaps stay
human-written** (`set_recap`) so they read like a person wrote them.

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

Restart Claude Code, then: *"refresh my contributions and sync."*

## Typical loop

1. `refresh` → pull the latest status + comments from GitHub.
2. If a recap is flagged empty: `pr_context(id)` → read it → `set_recap(id, "...")`.
3. `find_issues(repo)` → curate → `add_potential_pr/issue`.
4. `sync` → push; the dashboard updates.

## Data schema (the Hermes seam)

`data/contributions.json`:

```
meta               owner, focus, generated, updated_by ("mcp"|"hermes"|"manual"), schema
contributions[]    id, repo, project, lang, org, kind, url, title,
                   status (merged|review|awaiting|closed), created, ended,
                   additions, deletions, files, reviewer, review_decision,
                   recap, latest_comment{author,at,body}, next_action, blocked_on
potential_prs[]    repo, issue, lang, org, url, title, readiness:"ready", why, source
potential_issues[] same shape, readiness:"watch"
```

Any writer that preserves this schema can own updates — that's how Hermes replaces the MCP
without touching the dashboard.

[FastMCP]: https://github.com/modelcontextprotocol/python-sdk

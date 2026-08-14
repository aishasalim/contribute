# Hermes — the auto-apply loop

Hermes talks to the **API**, never to `data/roles.json`. A file cannot be the interface for an
agent that writes back: two clients reading and writing one file in git is a merge conflict
waiting to happen, and Hermes cannot mark a role applied without a round trip through a commit.

```
 ATS boards ──find_roles──▶ Postgres ──API──▶ Hermes ──apply──▶ employer
                              │                 │
                              │                 └──▶ Discord ──▶ you
                              └──▶ dashboard (radar.html)
```

You stay in the loop through Discord. You do not read the board unless you want to.

## The daily loop

| When | What | How |
|------|------|-----|
| 06:00 | Harvest | `find_roles(scope="priority")` → `db/sync.py push` |
| 06:10 | Digest | `notify.fresh_digest(...)` — one message, not one per role |
| 06:15 | Apply | `GET /queue` → claim → apply → report |
| 20:00 | Read email | Gmail scan → `POST /roles/{id}/status` on any rejection or advance |

## The apply loop, step by step

```
GET  /queue?limit=20&max_age=14        # already filtered to what is allowed
POST /roles/{id}/claim                 # 409 if another run took it — stop, do not retry
     ... fill the application ...
POST /roles/{id}/apply {"resume": "<best_track>", "notes": "..."}
```

If anything blocks:

```
POST /roles/{id}/flag {"reason": "asked why do you want to work here"}
```

`flag` resets the role to `none` so it stays open, records the reason, and pings you on
Discord. **Nothing was submitted.**

## The contract is in SQL, not in the prompt

`GET /queue` returns only what Hermes may act on. The rules are enforced by the query, so a
misreading of an instruction cannot widen them:

```sql
where tier = 'strong'          -- never a 'fit' or a 'stretch'
  and status = 'none'          -- never applied to twice
  and not dead
  and citizenship is null      -- no clearance or citizenship bar
  and paid is not false        -- never an unpaid posting
  and age_days <= 14
```

Three rules the API also enforces on write:

1. **The resume must be `best_track`.** `POST /apply` returns 422 for any other value. Hermes
   cannot decide to send the ML resume to a hardware role.
2. **`claim` is a conditional write.** It only succeeds while the status is still `none`, so
   two concurrent runs cannot both apply. The loser gets 409.
3. **Free text stops the run.** "Why this company", "describe a project" — Hermes flags and
   stops. It never writes an answer in your voice.

Hermes must also never answer a demographic or self-identification question. Leave blank.

## Discord messages you will get

| Message | When | Colour |
|---------|------|--------|
| Daily digest | Strong fits posted in the last 3 days, one message | blue |
| Hermes applied | An application went out, and which resume | green |
| **Needs you** | Hermes stopped; the role is still open | amber |
| Rejected | Found in email or set by hand | red |
| Offer | `@here` | green |

Only **Needs you** actually wants a reply.

## Email → status

A daily Gmail pass is the cheapest way to keep status current: rejections arrive by email and
nowhere else. Search the last day for the application senders, match the company against the
board, and post the change:

```
POST /roles/{id}/status {"status": "rejected", "source": "email"}
```

Match on company name plus a date window, never on the subject line alone. When the match is
ambiguous, flag it rather than guessing — the wrong role marked rejected is worse than a
missed one, because the role drops off the board.

## Auth

Reads are open when `API_TOKEN` is unset. **Writes always need the token** and fail closed if
it is missing:

```
Authorization: Bearer $API_TOKEN
```

Give Hermes the token. Nothing else needs it.

## Deploy

DigitalOcean App Platform, spec in [`.do/app.yaml`](../.do/app.yaml):

```bash
doctl apps create --spec .do/app.yaml
# then set DATABASE_URL, API_TOKEN, DISCORD_WEBHOOK as encrypted env vars
```

Point the dashboard at it once: `radar.html?api=https://<app>.ondigitalocean.app`. The URL is
kept in `localStorage`, so the page stays live after that. `?api=` clears it and falls back to
the static file.

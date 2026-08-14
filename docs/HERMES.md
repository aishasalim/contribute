# Hermes — the auto-apply loop

Hermes talks to **Postgres over HTTP**, never to `data/roles.json`. A file cannot be the
interface for an agent that writes back: two clients reading and writing one file in git is a
merge conflict waiting to happen, and Hermes cannot mark a role applied without a round trip
through a commit.

There is no server. Supabase exposes the view and the four write functions directly, so the
whole backend is SQL. Setup: [SETUP.md](SETUP.md).

```
 ATS boards ──find_roles──▶ Postgres (Supabase) ──▶ Hermes ──apply──▶ employer
                                  │                    │
                                  │                    └──▶ Discord ──▶ you
                                  └──▶ dashboard (radar.html)
```

You stay in the loop through Discord. You do not read the board unless you want to.

## The daily loop

| When | What | How |
|------|------|-----|
| 06:00 | Harvest | `find_roles(scope="priority")` → `db/sync.py push` |
| 06:10 | Digest | `notify.fresh_digest(...)` — one message, not one per role |
| 06:15 | Apply | read `hermes_queue` → claim → apply → report |
| 20:00 | Read email | Gmail scan → `set_role_status` on any rejection or advance |

## The apply loop, step by step

```
GET  /rest/v1/hermes_queue?limit=20     # already filtered to what is allowed
POST /rest/v1/rpc/claim_role            # ok:false if another run took it — stop
     ... fill the application ...
POST /rest/v1/rpc/apply_to_role         {"p_role_id": "...", "p_resume": "<best_track>"}
```

If anything blocks:

```
POST /rest/v1/rpc/flag_role  {"p_role_id": "...", "p_reason": "asked why do you want to work here"}
```

`flag_role` resets the role to `none` so it stays open, records the reason, and lets the
client ping you on Discord. **Nothing was submitted.**

## The contract is in SQL, not in the prompt

`hermes_queue` returns only what Hermes may act on. The rules live in the view, so a
misreading of an instruction cannot widen them:

```sql
where tier = 'strong'          -- never a 'fit' or a 'stretch'
  and status = 'none'          -- never applied to twice
  and not dead
  and citizenship is null      -- no clearance or citizenship bar
  and paid is not false        -- never an unpaid posting
  and age_days <= 14
```

Three rules the write functions also enforce:

1. **The resume must be `best_track`.** `apply_to_role` refuses any other value. Hermes cannot
   decide to send the ML resume to a hardware role.
2. **`claim_role` is a conditional write.** It only succeeds while the status is still `none`,
   so two concurrent runs cannot both apply. The loser gets `ok:false`.
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
POST /rest/v1/rpc/set_role_status {"p_role_id": "...", "p_status": "rejected", "p_source": "email"}
```

Match on company name plus a date window, never on the subject line alone. When the match is
ambiguous, flag it rather than guessing — the wrong role marked rejected is worse than a
missed one, because the role drops off the board.

## Auth

Two keys, and the split is the security model:

| Key | Holder | Can do |
|-----|--------|--------|
| `anon` | the dashboard — safe in a URL | read the board |
| `service_role` | **Hermes only, never committed** | claim, apply, set status, flag |

Row level security grants the anon key `select` and nothing else. The write path is not a
table grant at all: it is four `security definer` functions only `service_role` may execute.

## Calling it on Supabase

There is no server. PostgREST exposes the view and the functions directly, so Hermes talks to
Supabase over plain HTTP with the **`service_role`** key.

```bash
SB=$SUPABASE_URL/rest/v1
AUTH="apikey: $SUPABASE_SERVICE_KEY"
BEAR="Authorization: Bearer $SUPABASE_SERVICE_KEY"

# what may be applied to
curl -s "$SB/hermes_queue?limit=20" -H "$AUTH" -H "$BEAR"

# take it, then report it
curl -s -X POST "$SB/rpc/claim_role"    -H "$AUTH" -H "$BEAR" \
     -H 'Content-Type: application/json' -d '{"p_role_id":"<id>"}'
curl -s -X POST "$SB/rpc/apply_to_role" -H "$AUTH" -H "$BEAR" \
     -H 'Content-Type: application/json' -d '{"p_role_id":"<id>","p_resume":"hwv"}'

# blocked
curl -s -X POST "$SB/rpc/flag_role" -H "$AUTH" -H "$BEAR" \
     -H 'Content-Type: application/json' -d '{"p_role_id":"<id>","p_reason":"free-text essay"}'
```

Each function returns `{ok, detail}`. `claim_role` returns `ok:false` when another run already
took the role — stop there, do not retry.

Discord is sent by the client, not the database: call `api/notify.py` after a successful
write. Setup is in [SETUP.md](SETUP.md).

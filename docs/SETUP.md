# Setup — from a static page to a live board

The board works with no backend at all. `radar.html` reads `data/roles.json`, and a harvest
regenerates it. That is the whole product, and it costs nothing.

You only need the rest of this page when **Hermes** starts writing back. An agent cannot
share a JSON file in git with a dashboard: there is no way to mark a role applied without a
commit, and two writers race.

## Pick a backend

| | Free | Server to run | Setup | Good for |
|---|---|---|---|---|
| **Supabase** ← recommended | yes | **none** | ~10 min | what you want |
| Neon + self-hosted API | yes | one | ~30 min | if you dislike Supabase |
| Local Postgres in Docker | yes | one, on your Mac | ~15 min | Hermes runs only on this machine |
| DigitalOcean Managed PG | no (~$15/mo) | one | ~30 min | not worth it here |

**Supabase wins because it is Postgres plus an API.** `db/schema.sql` runs unchanged, and
PostgREST turns the tables into a REST endpoint on its own, so `api/main.py` never has to be
deployed. It stays in the repo for anyone self-hosting.

The one catch: a free Supabase project **pauses after 7 days with no requests**. A daily
Hermes run keeps it awake, so in practice it does not happen.

---

## Supabase, start to finish

### 1. Create the project

<https://supabase.com> → new project. Any region. Save the database password it shows you —
it appears once.

From **Project Settings → API**, copy:

| Key | Who gets it | Can do |
|-----|-------------|--------|
| Project URL | everyone | — |
| `anon` key | the dashboard, safe in a URL | read the board |
| `service_role` key | **Hermes only — never commit it** | apply, claim, set status |

### 2. Create the schema

**SQL Editor** → paste and run, in this order:

1. `db/schema.sql` — tables, indexes, the status-change trigger, the `radar` view
2. `db/supabase.sql` — row level security, the `hermes_queue` view, the four write functions

Row level security is what makes the anon key safe to publish: it grants `select` and nothing
else. Writes are not table grants at all — they are four `security definer` functions that
only `service_role` may execute.

### 3. Load the roles

From **Project Settings → Database → Connection string → URI**, then:

```bash
export DATABASE_URL='postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres'
uv run --with 'psycopg[binary]' python db/sync.py push
uv run --with 'psycopg[binary]' python db/sync.py stats
```

`push` upserts the roles and seeds an application row **only where none exists**, so it never
overwrites something you already sent.

### 4. Point the dashboard at it

Open it once with the project URL and the anon key:

```
radar.html?sb=https://<ref>.supabase.co&key=<anon key>
```

Both are stored in `localStorage`, so every later visit is live. The header then reads
`live (supabase)` instead of `snapshot`. `?sb=` with an empty value clears it and falls back
to the JSON file.

### 5. Give Hermes the secret key

```bash
export SUPABASE_URL='https://<ref>.supabase.co'
export SUPABASE_SERVICE_KEY='<service_role key>'
export DISCORD_WEBHOOK='https://discord.com/api/webhooks/...'
```

The daily loop and the exact calls are in [HERMES.md](HERMES.md).

---

## Keeping it fed

A harvest still runs from this machine or from CI:

```bash
python3 mcp/seed.py --priority          # ~5 min, the daily poll
uv run --with 'psycopg[binary]' python db/sync.py push
```

Nothing about the harvest changes when you add a backend. `roles.json` stays useful as the
offline snapshot and as the thing GitHub Pages serves.

## Discord

There is no server, so **the client sends the message**, not the database. `api/notify.py`
posts the embeds; Hermes calls it after a successful write. A failed Discord call never
blocks a write — Postgres is the record, Discord is only the ping.

Create the webhook in Discord: Server Settings → Integrations → Webhooks → New Webhook →
copy the URL. It is a secret; keep it in the environment, never in the repo.

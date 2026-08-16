# Local Hermes setup

## 1. Install system prerequisites

Ubuntu:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker "$USER"
```

Log out and back in after adding the Docker group. Docker is required for the
local database and API. Python dependencies and Chromium are managed by `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --extra test
uv run playwright install chromium
```

## 2. Configure local secrets and profile

```bash
cp .env.example .env
cp config/profile.example.toml config/profile.toml
chmod 600 .env config/profile.toml
mkdir -p resumes
```

In `.env`, set a long random `POSTGRES_PASSWORD` and `API_TOKEN`. Keep
`AUTO_SUBMIT=false`. Fill `config/profile.toml` and place all three resume PDFs
at the configured paths. These files are git-ignored.

Keep `APPLICATION_BATCH_LIMIT=2` for the pilot. `CANARY_ROLE_IDS` can contain
comma-separated role IDs when a dry run and its later live run must target the
same postings.

Notifications reuse the Discord bot already configured by Hermes. When there is
exactly one Discord DM in `~/.hermes/channel_directory.json`, no Discord setting
is needed. If Hermes knows multiple DMs, set `HERMES_DISCORD_TARGET` to the
intended target shown by `hermes send --list discord --json`.

Verify proactive delivery with:

```bash
hermes send --to discord:<dm-channel-id> "Hermes notification test"
```

Discord may reject proactive bot DMs with error `50278` when you and the bot no
longer share a server, even though interaction replies still work. Add Hermes
to a private server you belong to (you may continue chatting by DM), then retry.

## 3. Start Postgres and the API

```bash
docker compose up -d --build
set -a; . ./.env; set +a
DATABASE_URL="$HOST_DATABASE_URL" uv run python db/sync.py migrate
DATABASE_URL="$HOST_DATABASE_URL" uv run python db/sync.py push
curl http://127.0.0.1:8080/health
```

Postgres data lives in the `contribute_pgdata` Docker volume. The API and
database bind only to `127.0.0.1`. Use `docker compose logs -f` to diagnose
startup problems.

### Private phone review links

Install Tailscale on this always-on machine and your phone, sign both into the
same tailnet, then expose only the loopback API:

```bash
sudo tailscale up
tailscale serve --bg http://127.0.0.1:8080
tailscale serve status
```

Set `REVIEW_BASE_URL` in `.env` to the HTTPS Tailscale URL shown by the final
command. Review links use expiring random tokens, never change state on GET,
and remain private to authenticated tailnet devices.

## 4. Configure Gmail over IMAP

1. Turn on 2-Step Verification for the Google account.
2. Visit <https://myaccount.google.com/apppasswords> and create an app
   password named `hermes`.
3. Put it in `.env` as `GMAIL_APP_PASSWORD`, with the address in
   `GMAIL_ADDRESS`.
4. Run `set -a; . ./.env; set +a; uv run python -m hermes.gmail`.

An app password is scoped to mail, is revocable on its own without touching
the account password, and needs no consent browser — so the nightly job runs
headless with no token to refresh. The mailbox is opened readonly, so Hermes
can classify a reply but never mark, move or delete one. Revoke it from the
same app-passwords page to disconnect Hermes.

## 5. Verify dry-run behavior

```bash
set -a; . ./.env; set +a
uv run pytest -q
uv run python -m hermes.worker 1
```

The second command processes at most one queue role without submitting.
Inspect:

```sql
select * from application_attempts order by claimed_at desc;
select normalized_text, required, category, disposition, profile_key
from application_questions order by encountered_at desc;
```

Unsupported ATS pages and unclear required questions should become
`awaiting_human` and produce Discord messages. Do not enable live submission
until that behavior is confirmed.

For the two-role pilot, leave `AUTO_SUBMIT=false` and run:

```bash
uv run python -m hermes.worker 2
```

Each dry run sends a Discord details link. After reviewing both, set exact
`CANARY_ROLE_IDS`, temporarily run the same command with `AUTO_SUBMIT=true`,
one role at a time, then return `AUTO_SUBMIT=false`. Do not enable scheduled
live submission until both canaries have positive ATS confirmations.

## 6. Install the schedule

```bash
chmod +x scripts/install_cron.sh
scripts/install_cron.sh
crontab -l
```

The managed block runs harvest/apply at 11 AM, 1 PM, 3 PM, 5 PM, and 7 PM,
then Gmail at 8 PM in `America/Chicago`. It also drains the notification outbox
to your existing Hermes Discord DM every minute. Logs go to
`artifacts/cron.log` and `artifacts/notify.log`.

To disable all scheduled work, remove the block between
`# contribute-hermes-managed begin` and `end` from `crontab -e`. To keep
harvesting but prohibit final clicks, leave cron installed and set
`AUTO_SUBMIT=false`.

## 7. Enable live submission

After reviewing dry runs:

```bash
# edit .env
AUTO_SUBMIT=true
```

No restart is needed for cron because each invocation reloads `.env`. The API
still enforces eligibility, season, resume, lease, confirmation, and daily
company limits independently of the worker.

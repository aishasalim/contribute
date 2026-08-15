# Hermes local auto-apply

Hermes is a host-side Playwright worker. Postgres and FastAPI run in Docker;
the browser remains on the desktop so ATS login and anti-bot checks are visible.
The worker only receives roles that satisfy policy in the API, and the API
rechecks policy immediately before submission.

## Schedule

All times use `America/Chicago`.

| Time | Job |
|---|---|
| 11:00, 13:00, 15:00, 17:00, 19:00 | Harvest priority boards, sync Postgres, process queue |
| 20:00 | Read Gmail and reconcile application status |

`scheduled_runs` makes duplicate cron delivery harmless. Harvest/apply refuses
to run outside its allowed hours, and Gmail refuses outside 20:00.

## Submission contract

A role is claimable only when all of these are true:

- tier is `strong`;
- status is `none`;
- posting is live, no more than 14 days old, and in an allowed season;
- it is not explicitly unpaid and has no citizenship restriction;
- fewer than three applications have been submitted to that company today.

The resume must be the role's `best_track`. Claim and pre-submit checks are
transactional. One role can have only one active leased attempt.

The browser supports Greenhouse, Lever, and Ashby. Other ATS hosts, unsafe
redirects, login, CAPTCHA/MFA, missing resumes, and unknown required fields are
flagged for a human. Hermes:

- fills deterministic values only from `config/profile.toml`;
- records every encountered question, whether it was required, its category,
  disposition, profile source, and a value hash;
- never stores or answers demographic/self-identification values;
- skips optional free text and stops on required free text;
- marks an application submitted only after positive confirmation;
- records a post-click uncertainty as `unknown` and never retries it.

## Attempt states

`claimed -> preflight -> submitting -> submitted`

Stops become `awaiting_human`. A timeout after final click becomes `unknown`.
Dry-run preflights become `abandoned`; they do not change application state.
`attempt_events` is append-only and preserves the audit trail.

## Discord

Application state and a notification outbox row commit in one transaction.
The host notifier calls `hermes send`, reusing Hermes's existing Discord bot and
the discovered DM target—no webhook or second Discord credential is needed.
It retries with bounded exponential backoff, so a Discord outage does not lose
an applied, pending, unknown, rejection, interview, or offer alert.

## Gmail

The Gmail integration uses the read-only OAuth scope. It searches the last two
days but stores only message ID, timestamp, classification, matching evidence,
confidence, and decision—not email bodies.

Status changes require one unique match at confidence `>= 0.90`. Matching uses
requisition evidence where available, then company, sender domain, title, and
application window. Ambiguous messages leave status unchanged and send a
“Needs you” Discord alert. Subject-only matching and rejection-by-silence are
not allowed.

## Dry-run and live mode

`AUTO_SUBMIT=false` is the default. Hermes navigates, inventories, and fills the
form but does not click submit. Review the database question and attempt rows.
Set `AUTO_SUBMIT=true` in the local `.env` only after dry-run fixtures and live
preflights look correct. Set it back to `false` to stop submissions immediately.

See [SETUP.md](SETUP.md) for installation and credential setup.

-- contributie — internship radar, Postgres schema (DigitalOcean Managed Postgres)
--
-- Two tables, on purpose:
--   roles         is a cache of what the ATS boards say. A harvest overwrites it.
--   applications  is YOUR state. A harvest must never touch it.
--
-- That split is the whole point of a database here. The spreadsheet mixed both,
-- so a re-import risked overwriting an application you had already sent.
--
--   psql "$DATABASE_URL" -f db/schema.sql

create table if not exists roles (
    id            text primary key,
    company       text not null,
    title         text not null,
    location      text,
    workmode      text,
    season        text,
    url           text,
    source        text,
    posted        date,
    found         date not null,

    paid          boolean,          -- null = the posting does not say
    pay           text,             -- "$45.00 – $60.00 / hour" when stated

    sponsorship   boolean,
    citizenship   text,
    class_year    text[] default '{}',
    tags          text[] default '{}',

    score_swe     smallint default 0,
    score_ml      smallint default 0,
    score_hwv     smallint default 0,
    best_track    text,
    also_tracks   text[] default '{}',
    tier          text,

    why           text,
    why_by        text default 'auto',
    snippet       text,

    dead          boolean not null default false,
    first_seen    timestamptz not null default now(),
    last_seen     timestamptz not null default now(),

    constraint roles_tier_ck  check (tier in ('strong','fit','stretch','none')),
    constraint roles_track_ck check (best_track in ('swe','ml','hwv'))
);

create index if not exists roles_tier_idx    on roles (tier);
create index if not exists roles_track_idx   on roles (best_track);
create index if not exists roles_found_idx   on roles (found desc);
create index if not exists roles_posted_idx  on roles (posted desc nulls last);
create index if not exists roles_company_idx on roles (lower(company));
create index if not exists roles_tags_idx    on roles using gin (tags);

create table if not exists applications (
    role_id     text primary key references roles(id) on delete cascade,
    status      text not null default 'none',
    applied     date,
    resume      text,
    recruiter   text default '',
    network     text default '',
    thank_you   boolean not null default false,
    follow_up   boolean not null default false,
    notes       text default '',
    sheet_row   integer,            -- provenance: the spreadsheet row it came from
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),

    constraint applications_status_ck check (
        status in ('none','applied','in_progress','phone_screen','rejected','offer')),
    constraint applications_resume_ck check (resume is null or resume in ('swe','ml','hwv'))
);

create index if not exists applications_status_idx  on applications (status);
create index if not exists applications_applied_idx on applications (applied desc nulls last);

-- Every status change, kept forever. A rejection that arrives months later still
-- has a date you can reason about.
create table if not exists application_events (
    id          bigserial primary key,
    role_id     text not null references roles(id) on delete cascade,
    from_status text,
    to_status   text not null,
    at          timestamptz not null default now(),
    note        text default ''
);

create index if not exists application_events_role_idx on application_events (role_id, at desc);

create or replace function log_application_event() returns trigger as $$
begin
    if tg_op = 'INSERT' or old.status is distinct from new.status then
        insert into application_events (role_id, from_status, to_status, note)
        values (new.role_id,
                case when tg_op = 'UPDATE' then old.status else null end,
                new.status, coalesce(new.notes, ''));
    end if;
    new.updated_at := now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists applications_event_trg on applications;
create trigger applications_event_trg
    before insert or update on applications
    for each row execute function log_application_event();

-- One row per harvest, so you can see whether coverage is degrading.
create table if not exists harvests (
    id             bigserial primary key,
    ran_at         timestamptz not null default now(),
    scope          text,
    boards_ok      integer,
    boards_failed  integer,
    postings_seen  integer,
    roles_kept     integer,
    roles_new      integer
);

-- What the radar page reads: a role plus its application state.
create or replace view radar as
select r.*,
       coalesce(a.status, 'none') as status,
       a.applied, a.resume, a.recruiter, a.network,
       a.thank_you, a.follow_up, a.notes, a.sheet_row,
       greatest(r.score_swe, r.score_ml, r.score_hwv) as score,
       (current_date - coalesce(r.posted, r.found))    as age_days
from roles r
left join applications a on a.role_id = r.id;

-- Hermes execution state is separate from employer/application state.
create table if not exists application_attempts (
    id text primary key,
    role_id text not null references roles(id) on delete cascade,
    state text not null default 'claimed',
    worker_id text not null,
    lease_token_hash text not null,
    lease_expires_at timestamptz not null,
    policy_version text not null default '1',
    resume text not null,
    dry_run boolean not null default true,
    confirmation_number text,
    confirmation_url text,
    failure_code text,
    detail text default '',
    claimed_at timestamptz not null default now(),
    submitted_at timestamptz,
    updated_at timestamptz not null default now(),
    constraint attempts_state_ck check (
        state in ('claimed','preflight','awaiting_human','submitting',
                  'submitted','failed','unknown','abandoned')),
    constraint attempts_resume_ck check (resume in ('swe','ml','hwv'))
);

create unique index if not exists attempts_one_active_role_idx
on application_attempts(role_id)
where state in ('claimed','preflight','submitting');

create index if not exists attempts_lease_idx
on application_attempts(lease_expires_at)
where state in ('claimed','preflight','submitting');

create table if not exists attempt_events (
    id bigserial primary key,
    attempt_id text not null references application_attempts(id) on delete cascade,
    event text not null,
    detail text default '',
    at timestamptz not null default now()
);

create table if not exists application_questions (
    id bigserial primary key,
    attempt_id text not null references application_attempts(id) on delete cascade,
    role_id text not null references roles(id) on delete cascade,
    normalized_text text not null,
    field_type text not null,
    required boolean not null default false,
    category text not null,
    disposition text not null,
    profile_key text,
    answer_redacted text,
    answer_hash text,
    encountered_at timestamptz not null default now(),
    constraint demographic_answer_ck check (
        category <> 'demographic' or answer_redacted is null)
);

create index if not exists questions_role_idx
on application_questions(role_id, encountered_at desc);

create table if not exists application_review_requests (
    id text primary key,
    attempt_id text not null unique references application_attempts(id) on delete cascade,
    role_id text not null references roles(id) on delete cascade,
    token_hash text not null unique,
    kind text not null,
    state text not null default 'pending',
    payload jsonb not null default '{}'::jsonb,
    expires_at timestamptz not null,
    created_at timestamptz not null default now(),
    resolved_at timestamptz,
    constraint review_kind_ck check (kind in ('short_answer','human_handoff')),
    constraint review_state_ck check (
        state in ('pending','confirmed','edited','declined','expired'))
);

create index if not exists review_pending_idx
on application_review_requests(expires_at)
where state = 'pending';

create table if not exists application_answer_overrides (
    role_id text not null references roles(id) on delete cascade,
    normalized_text text not null,
    answer jsonb not null,
    source_review_id text not null references application_review_requests(id),
    created_at timestamptz not null default now(),
    primary key(role_id, normalized_text)
);

create table if not exists application_detail_tokens (
    id text primary key,
    attempt_id text not null references application_attempts(id) on delete cascade,
    token_hash text not null unique,
    expires_at timestamptz not null,
    created_at timestamptz not null default now()
);

create table if not exists scheduled_runs (
    job text not null,
    scheduled_for timestamptz not null,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    outcome text,
    detail text default '',
    primary key(job, scheduled_for)
);

create table if not exists email_observations (
    message_id text primary key,
    received_at timestamptz not null,
    classification text not null,
    matched_role_id text references roles(id) on delete set null,
    confidence numeric(4,3) not null default 0,
    evidence text default '',
    decision text not null,
    observed_at timestamptz not null default now()
);

create table if not exists integration_checkpoints (
    integration text primary key,
    cursor text not null,
    updated_at timestamptz not null default now()
);

create table if not exists notification_outbox (
    id bigserial primary key,
    event_type text not null,
    role_id text references roles(id) on delete set null,
    attempt_id text references application_attempts(id) on delete set null,
    dedupe_key text not null unique,
    payload jsonb not null,
    attempts integer not null default 0,
    next_attempt_at timestamptz not null default now(),
    delivered_at timestamptz,
    last_error text,
    created_at timestamptz not null default now()
);

create index if not exists outbox_pending_idx
on notification_outbox(next_attempt_at)
where delivered_at is null;

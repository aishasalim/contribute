-- Local Hermes: leases, audit trail, scheduling, Gmail evidence, notifications.

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

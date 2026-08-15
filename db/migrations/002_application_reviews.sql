-- Durable, one-time human review and role-scoped approved answers.

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

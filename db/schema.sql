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

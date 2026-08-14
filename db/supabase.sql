-- contributie — Supabase layer. Run AFTER db/schema.sql.
--
-- Why Supabase and not a server: it is Postgres, so db/schema.sql runs unchanged,
-- and PostgREST turns the tables and functions below into a REST API on its own.
-- There is no container to build, nothing to deploy, and the free tier costs
-- nothing. api/main.py stays in the repo for anyone who wants to self-host, but
-- it is no longer needed.
--
-- The security model maps exactly onto the two keys Supabase gives you:
--
--   anon key          public, safe in the dashboard   -> SELECT only
--   service_role key  secret, given ONLY to Hermes    -> may call the writers
--
-- Row level security is what enforces that. The write path is not a table grant;
-- it is four SECURITY DEFINER functions that keep the auto-apply contract in SQL
-- where a prompt cannot talk its way around it.

-- ---------------------------------------------------------------- lock it down
alter table roles              enable row level security;
alter table applications       enable row level security;
alter table application_events enable row level security;
alter table harvests           enable row level security;

-- Anyone with the anon key may read the board. Nobody may write through a table.
drop policy if exists roles_read on roles;
create policy roles_read on roles for select using (true);

drop policy if exists applications_read on applications;
create policy applications_read on applications for select using (true);

drop policy if exists events_read on application_events;
create policy events_read on application_events for select using (true);

drop policy if exists harvests_read on harvests;
create policy harvests_read on harvests for select using (true);

-- service_role bypasses RLS, so the harvest push keeps working unchanged.

-- ------------------------------------------------------------------- the queue
-- This view IS the auto-apply contract. Hermes reads it and nothing else, so the
-- rules cannot be widened by misreading an instruction.
create or replace view hermes_queue as
select id, company, title, location, workmode, season, url, pay,
       best_track as resume, score, age_days, why, tags
from radar
where tier = 'strong'
  and status = 'none'
  and not dead
  and citizenship is null      -- no clearance or citizenship bar
  and paid is not false        -- never an unpaid posting
  and age_days <= 14
order by age_days asc nulls last, score desc;

-- ------------------------------------------------------------- the write paths
-- Take a role before starting. The where clause is the lock: two concurrent runs
-- cannot both win, so the same posting is never applied to twice.
create or replace function claim_role(p_role_id text)
returns table (ok boolean, detail text)
language plpgsql security definer as $$
declare v_id text;
begin
    insert into applications (role_id, status, notes)
    values (p_role_id, 'in_progress', 'claimed by hermes')
    on conflict (role_id) do update set status = 'in_progress'
    where applications.status = 'none'
    returning role_id into v_id;

    if v_id is null then
        return query select false, 'already claimed or already applied';
    else
        return query select true, 'claimed';
    end if;
end; $$;

-- Report a submitted application. The resume MUST be the role's best track:
-- Hermes cannot decide to send the ML resume to a hardware role.
create or replace function apply_to_role(
    p_role_id text, p_resume text, p_notes text default '')
returns table (ok boolean, detail text)
language plpgsql security definer as $$
declare v_best text;
begin
    select best_track into v_best from roles where id = p_role_id;
    if v_best is null then
        return query select false, 'no such role'; return;
    end if;
    if p_resume is distinct from v_best then
        return query select false,
            format('resume must be %s, the role''s best track', v_best); return;
    end if;

    insert into applications (role_id, status, applied, resume, notes)
    values (p_role_id, 'applied', current_date, p_resume, p_notes)
    on conflict (role_id) do update set
        status = 'applied', applied = current_date,
        resume = excluded.resume, notes = excluded.notes;

    return query select true, 'applied';
end; $$;

-- A status change, typically a rejection found in the daily email pass.
create or replace function set_role_status(
    p_role_id text, p_status text, p_notes text default '', p_source text default 'manual')
returns table (ok boolean, detail text)
language plpgsql security definer as $$
begin
    if p_status not in ('none','applied','in_progress','phone_screen','rejected','offer') then
        return query select false, 'bad status'; return;
    end if;
    insert into applications (role_id, status, notes)
    values (p_role_id, p_status, coalesce(nullif(p_notes,''), 'via ' || p_source))
    on conflict (role_id) do update set
        status = p_status,
        notes  = coalesce(nullif(p_notes,''), applications.notes);
    return query select true, p_status;
end; $$;

-- Hermes could not finish. Put the role back on the board, record why, and let
-- the client send the Discord ping. Nothing was submitted.
create or replace function flag_role(p_role_id text, p_reason text)
returns table (ok boolean, detail text)
language plpgsql security definer as $$
begin
    insert into applications (role_id, status, notes)
    values (p_role_id, 'none', 'needs_human: ' || p_reason)
    on conflict (role_id) do update set
        status = 'none', notes = 'needs_human: ' || p_reason;
    return query select true, 'flagged';
end; $$;

-- Only the secret key may call the writers. The anon key keeps its read access.
revoke all on function claim_role(text)                        from anon, public;
revoke all on function apply_to_role(text, text, text)         from anon, public;
revoke all on function set_role_status(text, text, text, text) from anon, public;
revoke all on function flag_role(text, text)                   from anon, public;

grant execute on function claim_role(text)                        to service_role;
grant execute on function apply_to_role(text, text, text)         to service_role;
grant execute on function set_role_status(text, text, text, text) to service_role;
grant execute on function flag_role(text, text)                   to service_role;

grant select on radar, hermes_queue to anon, authenticated, service_role;

-- ---------------------------------------------------------------- what to call
-- Board, freshest first:
--   GET /rest/v1/radar?tier=eq.strong&status=eq.none&order=age_days.asc&limit=50
--     apikey: <anon>
--
-- The queue:
--   GET /rest/v1/hermes_queue?limit=20
--
-- Writes (service_role key only):
--   POST /rest/v1/rpc/claim_role      {"p_role_id": "..."}
--   POST /rest/v1/rpc/apply_to_role   {"p_role_id": "...", "p_resume": "hwv"}
--   POST /rest/v1/rpc/set_role_status {"p_role_id": "...", "p_status": "rejected"}
--   POST /rest/v1/rpc/flag_role       {"p_role_id": "...", "p_reason": "..."}

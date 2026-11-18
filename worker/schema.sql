-- One row per unique role, however many sources listed it.
create table if not exists roles (
    id            bigint generated always as identity primary key,
    company       text not null,
    title         text not null,
    url           text not null,
    locations     text[] not null default '{}',
    category      text,
    terms         text[] not null default '{}',
    sources       text[] not null default '{}',
    posted_at     timestamptz,
    first_seen_at timestamptz not null default now(),
    -- null = notification still pending
    notified_at   timestamptz
);

create index if not exists roles_first_seen_idx on roles (first_seen_at desc);
create index if not exists roles_unnotified_idx on roles (id) where notified_at is null;

-- Keys that identify a role: src:<source>:<listing id>, and the posting's
-- job ID (job:<system>:<id>, or url:<cleaned url> when there's no ID).
-- Listings with the same job ID are the same role.
create table if not exists role_keys (
    key        text primary key,
    role_id    bigint not null references roles (id) on delete cascade,
    created_at timestamptz not null default now()
);

-- Job IDs a source marked grad-only, so copies from sources without
-- degree data are filtered too.
create table if not exists excluded_keys (
    key        text primary key,
    created_at timestamptz not null default now()
);

-- Supabase exposes public tables through its REST API using a public key.
-- RLS with no policies blocks that API; the worker connects as the table
-- owner and is unaffected. The dashboard gets a read-only policy later.
alter table roles enable row level security;
alter table role_keys enable row level security;
alter table excluded_keys enable row level security;

-- Dashboard: the public (anon) key may read roles and nothing else. No
-- insert/update/delete policies exist, so it can't write; role_keys and
-- excluded_keys have no policies, so it can't read them either.
do $$
begin
    if not exists (select 1 from pg_policies where tablename = 'roles' and policyname = 'Public read roles') then
        create policy "Public read roles" on roles for select to anon, authenticated using (true);
    end if;
    -- Stream inserts/updates to the dashboard (Supabase Realtime).
    if exists (select 1 from pg_publication where pubname = 'supabase_realtime')
       and not exists (select 1 from pg_publication_tables
                       where pubname = 'supabase_realtime' and tablename = 'roles') then
        alter publication supabase_realtime add table roles;
    end if;
end $$;

-- "Applied" button on the dashboard. The dashboard's public key can't update
-- roles, so it calls set_applied with your passcode; only a sha256 of the
-- passcode is stored, in a table the public key can't read.
alter table roles add column if not exists applied_at timestamptz;

create table if not exists dashboard_passcode (
    id   boolean primary key default true check (id),
    hash text not null
);
alter table dashboard_passcode enable row level security;

-- Returns true if the role's state changed (so the caller adds the sheet row
-- once), false if it was already that way. Raises on a wrong passcode.
create or replace function set_applied(p_role_id bigint, p_passcode text, p_applied boolean)
returns boolean
language plpgsql security definer set search_path = public, extensions
as $$
declare changed int;
begin
    if not exists (select 1 from dashboard_passcode
                   where hash = encode(digest(p_passcode, 'sha256'), 'hex')) then
        raise exception 'wrong passcode' using errcode = '28P01';
    end if;
    update roles set applied_at = case when p_applied then now() end
     where id = p_role_id and (applied_at is null) = p_applied;
    get diagnostics changed = row_count;
    return changed > 0;
end $$;
revoke all on function set_applied(bigint, text, boolean) from public;
grant execute on function set_applied(bigint, text, boolean) to anon, authenticated;

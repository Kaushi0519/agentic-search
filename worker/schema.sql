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

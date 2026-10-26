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

"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CATEGORIES,
  TERM_GROUPS,
  type Category,
  type Role,
  type TermGroup,
  categoryOf,
  fetchAllRoles,
  formatAge,
  supabase,
  termGroupOf,
  termsOf,
} from "@/lib/roles";

const PAGE = 100;
const WINDOWS = { all: Infinity, "24h": 24 * 3600_000, "7d": 7 * 24 * 3600_000 } as const;
type Window = keyof typeof WINDOWS;

function byNewest(a: Role, b: Role) {
  return b.first_seen_at.localeCompare(a.first_seen_at) || b.id - a.id;
}

export default function RolesBoard() {
  const [roles, setRoles] = useState<Role[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [fresh, setFresh] = useState<Set<number>>(new Set());
  const [now, setNow] = useState(() => Date.now());

  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<Category | "All">("All");
  const [term, setTerm] = useState<TermGroup | "All">("All");
  const [added, setAdded] = useState<Window>("all");
  const [limit, setLimit] = useState(PAGE);

  useEffect(() => {
    fetchAllRoles()
      .then(setRoles)
      .catch((e) => setError(e.message ?? String(e)));

    if (!supabase) return;
    const client = supabase;
    const channel = client
      .channel("roles-feed")
      .on("postgres_changes", { event: "*", schema: "public", table: "roles" }, (payload) => {
        if (payload.eventType === "DELETE") {
          const { id } = payload.old as Pick<Role, "id">;
          setRoles((prev) => prev && prev.filter((r) => r.id !== id));
          return;
        }
        const role = payload.new as Role;
        setRoles((prev) => {
          if (!prev) return prev;
          const rest = prev.filter((r) => r.id !== role.id);
          return [role, ...rest].sort(byNewest);
        });
        if (payload.eventType === "INSERT") setFresh((prev) => new Set(prev).add(role.id));
      })
      .subscribe((status) => setLive(status === "SUBSCRIBED"));

    const tick = setInterval(() => setNow(Date.now()), 30_000);
    return () => {
      clearInterval(tick);
      client.removeChannel(channel);
    };
  }, []);

  const filtered = useMemo(() => {
    if (!roles) return [];
    const q = query.trim().toLowerCase();
    const since = now - WINDOWS[added];
    return roles.filter(
      (r) =>
        (category === "All" || categoryOf(r.category) === category) &&
        (term === "All" || termGroupOf(r.terms) === term) &&
        new Date(r.first_seen_at).getTime() >= since &&
        (!q ||
          r.company.toLowerCase().includes(q) ||
          r.title.toLowerCase().includes(q) ||
          r.locations.some((l) => l.toLowerCase().includes(q))),
    );
  }, [roles, query, category, term, added, now]);

  const counts = useMemo(() => {
    const c = new Map<Category, number>();
    for (const r of roles ?? []) c.set(categoryOf(r.category), (c.get(categoryOf(r.category)) ?? 0) + 1);
    return c;
  }, [roles]);

  if (!supabase) {
    return (
      <p className="notice">
        Missing <code>NEXT_PUBLIC_SUPABASE_URL</code> / <code>NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY</code>.
      </p>
    );
  }

  const visible = filtered.slice(0, limit);

  return (
    <main className="board">
      <header className="header">
        <div>
          <h1>Internships</h1>
          <p className="subtitle">
            US · undergrad · {roles ? roles.length.toLocaleString() : "…"} roles
            <span className={live ? "status live" : "status"}>{live ? "Live" : "Connecting…"}</span>
          </p>
        </div>
        <input
          className="search"
          type="search"
          placeholder="Search company, role or location"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setLimit(PAGE);
          }}
        />
      </header>

      <div className="filters">
        <div className="chips" role="group" aria-label="Category">
          {(["All", ...CATEGORIES] as const).map((c) => (
            <button
              key={c}
              className={category === c ? "chip active" : "chip"}
              onClick={() => {
                setCategory(c);
                setLimit(PAGE);
              }}
            >
              {c}
              {c !== "All" && <span className="count">{counts.get(c) ?? 0}</span>}
            </button>
          ))}
        </div>
        <div className="chips" role="group" aria-label="Term">
          {(["All", ...TERM_GROUPS] as const).map((t) => (
            <button
              key={t}
              className={term === t ? "chip active" : "chip"}
              onClick={() => {
                setTerm(t);
                setLimit(PAGE);
              }}
            >
              {t === "All" ? "All terms" : t}
            </button>
          ))}
        </div>
        <div className="chips" role="group" aria-label="Added">
          {(Object.keys(WINDOWS) as Window[]).map((w) => (
            <button
              key={w}
              className={added === w ? "chip active" : "chip"}
              onClick={() => {
                setAdded(w);
                setLimit(PAGE);
              }}
            >
              {w === "all" ? "Any time" : `Last ${w}`}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="notice">Couldn’t load roles: {error}</p>}
      {!roles && !error && <p className="notice">Loading roles…</p>}

      {roles && (
        <>
          <table className="roles">
            <thead>
              <tr>
                <th>Company</th>
                <th>Role</th>
                <th>Location</th>
                <th>Term</th>
                <th>Application</th>
                <th>Age</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((r, i) => (
                <tr key={r.id} className={fresh.has(r.id) ? "fresh" : undefined}>
                  <td className="company">
                    {i > 0 && visible[i - 1].company === r.company ? (
                      <span className="same" title={r.company}>↳</span>
                    ) : (
                      r.company
                    )}
                  </td>
                  <td className="title">{r.title}</td>
                  <td className="location">
                    <Locations locations={r.locations} />
                  </td>
                  <td className="term">
                    <Terms terms={termsOf(r.terms)} />
                  </td>
                  <td>
                    <a className="apply" href={r.url} target="_blank" rel="noopener noreferrer">
                      Apply
                    </a>
                  </td>
                  <td className="age">{formatAge(r.first_seen_at, now)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {filtered.length === 0 && <p className="notice">No roles match.</p>}
          {filtered.length > limit && (
            <button className="more" onClick={() => setLimit((n) => n + PAGE)}>
              Show more ({(filtered.length - limit).toLocaleString()} left)
            </button>
          )}
        </>
      )}
    </main>
  );
}

function Locations({ locations }: { locations: string[] }) {
  if (locations.length === 0) return <span className="muted">—</span>;
  if (locations.length === 1) return <>{locations[0]}</>;
  return (
    <details>
      <summary>{locations.length} locations</summary>
      <ul>
        {locations.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </details>
  );
}

function Terms({ terms }: { terms: string[] }) {
  if (terms.length === 0) return <span className="muted">—</span>;
  return (
    <>
      {terms.map((t) => (
        <span key={t} className={/summer/i.test(t) ? "term-tag summer" : "term-tag"}>
          {t}
        </span>
      ))}
    </>
  );
}

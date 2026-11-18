"use client";

import { useEffect, useMemo, useRef, useState } from "react";
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
  systemOf,
  termGroupOf,
  termsOf,
} from "@/lib/roles";

const PAGE = 100;
const PASSCODE_KEY = "dashboard-passcode";

function readPasscode(): string {
  try {
    return localStorage.getItem(PASSCODE_KEY) ?? "";
  } catch {
    return "";
  }
}

function storePasscode(value: string) {
  try {
    if (value) localStorage.setItem(PASSCODE_KEY, value);
    else localStorage.removeItem(PASSCODE_KEY);
  } catch {
    // Private browsing: it just won't be remembered.
  }
}
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
  const [hideApplied, setHideApplied] = useState(false);

  // "Applied" button: the passcode is asked once per browser, then remembered.
  const [asking, setAsking] = useState<{ id: number; applied: boolean } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState<Set<number>>(new Set());

  async function markApplied(id: number, applied: boolean, code = readPasscode()) {
    if (!code) {
      setAsking({ id, applied });
      return;
    }
    setSaving((prev) => new Set(prev).add(id));
    setMessage(null);
    try {
      const res = await fetch("/api/applied", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, passcode: code, applied }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.status === 401) {
        storePasscode("");
        setAsking({ id, applied });
        setMessage("Wrong passcode. Try again.");
        return;
      }
      if (!res.ok) {
        setMessage(body.error ?? `Something went wrong (${res.status}).`);
        return;
      }
      storePasscode(code);
      setAsking(null);
      const appliedAt = applied ? new Date().toISOString() : null;
      setRoles((prev) => prev && prev.map((r) => (r.id === id ? { ...r, applied_at: appliedAt } : r)));
      if (applied && body.sheet) setMessage(`Added to your tracker (row ${body.row}).`);
      if (!applied) setMessage("Unmarked. The spreadsheet row stays; delete it there if you need to.");
    } catch (e) {
      setMessage(`Couldn't reach the dashboard server: ${e instanceof Error ? e.message : e}`);
    } finally {
      setSaving((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

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
        (!hideApplied || !r.applied_at) &&
        new Date(r.first_seen_at).getTime() >= since &&
        (!q ||
          r.company.toLowerCase().includes(q) ||
          r.title.toLowerCase().includes(q) ||
          r.locations.some((l) => l.toLowerCase().includes(q))),
    );
  }, [roles, query, category, term, added, hideApplied, now]);

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
          <button
            className={hideApplied ? "chip active" : "chip"}
            aria-pressed={hideApplied}
            onClick={() => {
              setHideApplied((h) => !h);
              setLimit(PAGE);
            }}
          >
            Hide applied
          </button>
        </div>
      </div>

      {asking && (
        <PasscodeForm
          onSubmit={(code) => markApplied(asking.id, asking.applied, code)}
          onCancel={() => {
            setAsking(null);
            setMessage(null);
          }}
        />
      )}
      {message && (
        <p className="toast" role="status">
          {message}
        </p>
      )}

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
                <th>System</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((r, i) => (
                <tr
                  key={r.id}
                  className={[fresh.has(r.id) && "fresh", r.applied_at && "applied"].filter(Boolean).join(" ") || undefined}
                >
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
                    <div className="actions">
                      <a className="apply" href={r.url} target="_blank" rel="noopener noreferrer">
                        Apply
                      </a>
                      <AppliedButton
                        applied={!!r.applied_at}
                        saving={saving.has(r.id)}
                        onConfirm={(applied) => markApplied(r.id, applied)}
                      />
                    </div>
                  </td>
                  <td className="age">{formatAge(r.first_seen_at, now)}</td>
                  <td className="system">{systemOf(r.url)}</td>
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

/** Two taps so a stray click doesn't add a spreadsheet row: "Applied?" → "Confirm". */
function AppliedButton({
  applied,
  saving,
  onConfirm,
}: {
  applied: boolean;
  saving: boolean;
  onConfirm: (applied: boolean) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  if (saving) {
    return (
      <button className="mark" disabled>
        Saving…
      </button>
    );
  }
  if (confirming) {
    return (
      <button
        className="mark confirm"
        onClick={() => {
          setConfirming(false);
          onConfirm(!applied);
        }}
      >
        {applied ? "Undo?" : "Confirm"}
      </button>
    );
  }
  return (
    <button
      className={applied ? "mark done" : "mark"}
      title={applied ? "Applied. Tap to unmark." : "Mark applied and add it to your tracker sheet"}
      onClick={() => {
        setConfirming(true);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setConfirming(false), 4000);
      }}
    >
      {applied ? "✓ Applied" : "Applied?"}
    </button>
  );
}

function PasscodeForm({ onSubmit, onCancel }: { onSubmit: (code: string) => void; onCancel: () => void }) {
  const [code, setCode] = useState("");
  return (
    <form
      className="passcode"
      onSubmit={(e) => {
        e.preventDefault();
        if (code.trim()) onSubmit(code.trim());
      }}
    >
      <label htmlFor="passcode">Dashboard passcode (asked once on this device)</label>
      <input
        id="passcode"
        type="password"
        autoComplete="current-password"
        autoFocus
        value={code}
        onChange={(e) => setCode(e.target.value)}
      />
      <button type="submit" className="mark confirm">
        Save
      </button>
      <button type="button" className="mark" onClick={onCancel}>
        Cancel
      </button>
    </form>
  );
}

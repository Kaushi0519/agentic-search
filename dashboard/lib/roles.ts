import { createClient } from "@supabase/supabase-js";

export type Role = {
  id: number;
  company: string;
  title: string;
  url: string;
  locations: string[];
  category: string | null;
  terms: string[] | null;
  sources: string[];
  first_seen_at: string;
};

export const ROLE_COLUMNS = "id, company, title, url, locations, category, terms, sources, first_seen_at";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

// The publishable key is meant to be public; RLS only lets it read `roles`.
export const supabase = url && key ? createClient(url, key) : null;

export const CATEGORIES = ["Software", "AI/ML/Data", "Hardware", "Quant", "Product", "Security", "Other"] as const;
export type Category = (typeof CATEGORIES)[number];

/** Sources name categories differently ("Data & ML/AI", "Software Engineering"…). */
export function categoryOf(raw: string | null): Category {
  const s = (raw ?? "").toLowerCase();
  if (s.includes("quant")) return "Quant";
  if (s.includes("security")) return "Security";
  if (s.includes("hardware")) return "Hardware";
  if (s.includes("product")) return "Product";
  if (/\b(ai|ml|data|machine learning)\b/.test(s)) return "AI/ML/Data";
  if (s.includes("software")) return "Software";
  return "Other";
}

export const TERM_GROUPS = ["Summer", "Off-season"] as const;
export type TermGroup = (typeof TERM_GROUPS)[number];

/** Lists say "Summer 2027", "Summer" or "N/A"; drop the placeholders. */
export function termsOf(terms: string[] | null): string[] {
  return (terms ?? []).filter((t) => t && t !== "N/A");
}

/** Summer if any term is a summer; off-season if it only runs in fall, winter or spring. */
export function termGroupOf(terms: string[] | null): TermGroup | null {
  const known = termsOf(terms);
  if (known.some((t) => /summer/i.test(t))) return "Summer";
  if (known.some((t) => /fall|winter|spring/i.test(t))) return "Off-season";
  return null;
}

/** Same style as the GitHub lists: now, 12m, 5h, 2d. */
export function formatAge(iso: string, now: number): string {
  const minutes = Math.floor((now - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  if (minutes < 60 * 24) return `${Math.floor(minutes / 60)}h`;
  return `${Math.floor(minutes / (60 * 24))}d`;
}

export async function fetchAllRoles(): Promise<Role[]> {
  if (!supabase) return [];
  const pageSize = 1000; // Supabase caps a single response at 1,000 rows
  const roles: Role[] = [];
  for (let from = 0; ; from += pageSize) {
    const { data, error } = await supabase
      .from("roles")
      .select(ROLE_COLUMNS)
      .order("first_seen_at", { ascending: false })
      .order("id", { ascending: false })
      .range(from, from + pageSize - 1);
    if (error) throw error;
    roles.push(...(data as Role[]));
    if (data.length < pageSize) return roles;
  }
}

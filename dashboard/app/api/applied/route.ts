import { type Role, resumeFor, supabase, systemOf } from "@/lib/roles";

/**
 * POST {"id": 123, "passcode": "…", "applied": true}
 *
 * Marks a role applied (or not) through the set_applied database function, which checks
 * the passcode. When a role is newly marked applied, it also adds a row to the
 * application tracker sheet (apply/tracker.gs). If the sheet write fails, the mark is
 * undone so you can try again.
 */
export async function POST(request: Request) {
  if (!supabase) return Response.json({ error: "Supabase isn't configured" }, { status: 500 });
  const { id, passcode, applied } = (await request.json()) as { id: number; passcode: string; applied: boolean };
  if (!Number.isInteger(id) || typeof passcode !== "string" || typeof applied !== "boolean") {
    return Response.json({ error: "Bad request" }, { status: 400 });
  }

  const { data: changed, error } = await supabase.rpc("set_applied", {
    p_role_id: id,
    p_passcode: passcode,
    p_applied: applied,
  });
  if (error) {
    const wrong = error.message.includes("wrong passcode");
    return Response.json({ error: wrong ? "Wrong passcode" : error.message }, { status: wrong ? 401 : 500 });
  }
  // Unmarking leaves the sheet alone, and so does marking a role that was already applied.
  if (!applied || !changed) return Response.json({ ok: true, sheet: false });

  try {
    const { data: role, error: readError } = await supabase
      .from("roles")
      .select("company, title, url, locations, category")
      .eq("id", id)
      .single<Pick<Role, "company" | "title" | "url" | "locations" | "category">>();
    if (readError) throw new Error(readError.message);
    const row = await addTrackerRow(role);
    return Response.json({ ok: true, sheet: true, row });
  } catch (e) {
    await supabase.rpc("set_applied", { p_role_id: id, p_passcode: passcode, p_applied: false });
    const message = e instanceof Error ? e.message : String(e);
    return Response.json({ error: `Couldn't add the spreadsheet row: ${message}` }, { status: 502 });
  }
}

async function addTrackerRow(role: Pick<Role, "company" | "title" | "url" | "locations" | "category">) {
  const url = process.env.TRACKER_URL;
  const secret = process.env.TRACKER_SECRET;
  if (!url || !secret) throw new Error("TRACKER_URL / TRACKER_SECRET aren't set in Vercel");

  // Your date, not the server's (Vercel runs in UTC).
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
  // Apps Script answers the POST with a redirect to the result; fetch follows it with a GET.
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      secret,
      row: {
        Company: role.company,
        Role: role.title,
        Location: role.locations.join("; "),
        "Application Date": today,
        Status: "Applied",
        "Resume Version": resumeFor(role.title),
        "Cover Letter": "No",
        Notes: `Applied (${systemOf(role.url)}) ${role.url}`,
      },
    }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || !body.ok) throw new Error(body.error ?? `tracker answered ${res.status}`);
  return body.row as number;
}

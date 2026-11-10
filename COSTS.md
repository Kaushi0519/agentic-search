# Costs

Estimated monthly cost of running Agentic Search. Update this when a service, plan or usage changes, and add a line to the log at the bottom.

## Current estimate: about $6.50–7/month

| Service | Plan | What it does | Per month | Billing |
|---|---|---|---|---|
| Railway | Hobby | Runs the scout worker (polls the lists, sends alerts) | $5.00 | Monthly subscription; includes $5 of usage, which the worker stays under |
| Anthropic API | Pay as you go | Haiku (`claude-haiku-5-5`) writes form answers for auto-apply | ~$1.50–2.00 | Prepaid credits ($5 lasts ~3 months at this rate) |
| Vercel | Hobby | Hosts the dashboard | $0 | Free |
| Supabase | Free | Postgres database and Realtime | $0 | Free |
| Telegram | — | Alert bot and apply bot | $0 | Free |
| GitHub | Free | Repo, and API access to the internship lists | $0 | Free |
| Google Sheets + Apps Script | — | Application tracker and the web app that writes to it | $0 | Free |
| Your Mac | — | Runs the auto-apply browser | $0 | Electricity only |
| **Total** | | | **~$6.50–7.00** | |

## How the Anthropic estimate is worked out

- Haiku 5.5: $0.10 per million input tokens, $0.50 per million output tokens.
- One application ≈ 8–12K input tokens (profile, both resumes, answer bank, job description, questions) and 1–4K output tokens (longer written answers) ≈ **0.3–0.4¢**.
- Supported roles (Greenhouse + Ashby + Lever) added in the last 14 days: 239 → **~500 a month** → **~$1.50–2/month**.
- Only roles on supported systems use Haiku; other roles are skipped without a call.

To check the real spend: console.anthropic.com → **Usage** / **Billing**.

## What would change it

| Change | Effect on the monthly total |
|---|---|
| Support Workday, Oracle and other systems (~7× as many roles) | Anthropic rises to ~$5–9, so the total is ~$10–14 |
| Cloud browser, so applications go out while the Mac is off | +$20–50 |
| Railway worker using more than the $5 included | Pay for the extra usage (not expected) |
| Supabase over its free limits (500 MB database) | Pro plan, $25 (not expected) |

## Log

| Date | Change | Monthly estimate |
|---|---|---|
| 2026-10 | Scout worker on Railway Hobby; Supabase, Vercel and Telegram on free plans | $5 |
| 2026-11-09 | Auto-apply with Haiku (Greenhouse); $5 Anthropic credits bought | ~$6 |
| 2026-11-10 | Ashby support and the Telegram apply bot (free) | ~$6 |
| 2026-11-10 | Lever support (more roles); answers now read both resumes and the answer bank (bigger prompts) | ~$6.50–7 |

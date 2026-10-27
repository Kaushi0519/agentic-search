# Agentic Search

Automates the internship search. A worker watches the big Summer 2027 internship lists on GitHub, keeps only **US, undergrad** roles, removes duplicates across lists, and sends each new role **once** to Telegram (iPhone + Mac) and a live dashboard.

**Dashboard:** https://agentic-search-gamma.vercel.app

## How it works

```
GitHub lists ──poll every 60s──▶ worker (Railway) ──▶ Supabase Postgres ──Realtime──▶ dashboard (Vercel)
                                       │
                                       └──▶ Telegram bot ──▶ iPhone + Mac
```

**Sources**, read from the data files the READMEs are generated from (so all seasons, and often ahead of the README):

| Repo | File |
|---|---|
| [SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships) | `.github/scripts/listings.json` (`dev`) |
| [vanshb03/Summer2027-Internships](https://github.com/vanshb03/Summer2027-Internships) | `.github/scripts/listings.json` (`dev`) |
| [zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships](https://github.com/zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships) | `data/jobs.json` (`main`) |

The worker checks each file's latest commit with ETags (unchanged checks are free) and only downloads it when it changes, pinned to the commit SHA.

**Filters**

- **Undergrad only:** drops titles mentioning new grad, master's, PhD, MBA, etc., and Simplify roles whose degree list excludes Bachelor's/Associate's. A job rejected as grad-only on one list is blocked on the others too, since only Simplify has degree data.
- **US only:** each location is classified US / foreign / unknown. Roles with only foreign locations are dropped; multi-country roles keep just their US locations.
- Only open roles are loaded; roles that close later stay on the dashboard.

**Dedup by job ID.** Each posting URL is reduced to its ATS job ID (Greenhouse, Workday requisition, Lever/Ashby UUIDs, numeric IDs on company sites, …), falling back to the canonical URL. The same job on several lists becomes one role with several sources. Different requisitions with the same title (e.g. multiple Amazon SDE reqs) stay separate. Vansh's short links are resolved first.

**Notifications.** One Telegram message per new role: company, title, location(s), age and an Apply button. Unsent messages are retried after restarts. A source's first load is stored silently so you're not flooded with existing roles. If a source fails 10 polls in a row, the bot sends a warning (and a message when it recovers).

## Repo layout

```
worker/       Python worker (scout package), schema.sql, tests
dashboard/    Next.js dashboard
```

## Setup

### 1. Supabase

Create a project and run `worker/schema.sql` in the SQL editor. It creates the tables, enables row-level security (the public key can only read `roles`) and turns on Realtime. It's safe to re-run.

### 2. Telegram

Create a bot with [@BotFather](https://t.me/BotFather) and press Start in a chat with it. Once `TELEGRAM_BOT_TOKEN` is in `worker/.env` (step 3), get your chat ID:

```bash
cd worker
python scripts/telegram_chat_id.py
```

### 3. Worker

```bash
cd worker
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in the values below
```

| Variable | Value |
|---|---|
| `DATABASE_URL` | Supabase **Session pooler** connection string |
| `GITHUB_TOKEN` | Fine-grained token, public repo read-only |
| `TELEGRAM_BOT_TOKEN` | From BotFather |
| `TELEGRAM_CHAT_ID` | From the script above |
| `POLL_SECONDS` | Default `60` |

```bash
python -m scout.main            # run forever
python -m scout.main --once     # one poll, then exit
python -m scout.main --dry-run  # in-memory store, print instead of sending
python -m pytest tests          # tests
```

**Deploying on Railway:** set the root directory to `/worker` and add the same variables. `railway.json` sets the start command and restart policy.

### 4. Dashboard

See [dashboard/README.md](dashboard/README.md). On Vercel, set the root directory to `dashboard` and add `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`.

## Cost

Supabase, Vercel (Hobby), Telegram and the GitHub API are free at this scale. Railway is the only paid piece (Hobby plan, about $5/month).

## Roadmap

- [x] Worker: polling, filters, dedup, Telegram alerts
- [x] Deploy on Railway + Supabase
- [x] Live dashboard
- [ ] Instagram stories (zero2sudo): currently checked manually
- [ ] Assisted apply

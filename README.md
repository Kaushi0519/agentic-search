# Agentic Search

Automates the internship search. A worker watches the big Summer 2027 internship lists on GitHub, keeps only **US, undergrad** roles, removes duplicates across lists, and sends each new role **once** to Telegram (iPhone + Mac) and a live dashboard. An auto-apply bot on your Mac then fills the application for each new Greenhouse, Ashby or Lever role and asks for your OK in a separate Telegram chat before submitting, logging every application to a Google Sheet. A third bot writes answers to the application questions you send it.

**Dashboard:** https://agentic-search-gamma.vercel.app

## How it works

```
GitHub lists ──poll every 60s──▶ worker (Railway) ──▶ Supabase Postgres ──Realtime──▶ dashboard (Vercel)
                                       │                     │
                                       │                     └──▶ auto-apply bot (your Mac) ──▶ application forms
                                       │                                 │                └──▶ tracker (Google Sheet)
                                       └──▶ alert bot ──▶ Telegram       └──▶ apply bot ──▶ Telegram (Submit / Skip)
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
- The grad check also reads the posting URL's own title, since lists sometimes rewrite titles (a "Graduate_Careers" posting listed as an internship is still caught).
- Apple postings get their real locations from Apple's jobs API, since one list labels every Apple job "United States".
- Roles whose terms have all ended (e.g. only Summer 2026) are skipped.
- Only open roles are loaded; roles that close later stay on the dashboard.

**Dedup by job ID.** Each posting URL is reduced to its ATS job ID (Greenhouse, Workday requisition, Lever/Ashby UUIDs, numeric IDs on company sites, …), falling back to the canonical URL. The same job on several lists becomes one role with several sources. Different requisitions with the same title (e.g. multiple Amazon SDE reqs) stay separate. Vansh's short links are resolved first.

**Notifications.** One Telegram message per new role: company, title, location(s), age and an Apply button. Only roles posted in the last 2 days alert; older ones (often reopened postings) go on the dashboard quietly. Unsent messages are retried after restarts. A source's first load is stored silently so you're not flooded with existing roles. If a source fails 10 polls in a row, the bot sends a warning (and a message when it recovers).

**Dashboard.** A live table styled like the lists' READMEs: company, role, location, term (Summer / off-season, with a filter), the posting's application system (Workday, Greenhouse, …) and age. New roles appear without a refresh.

## Auto-apply

`apply/` is a bot that runs on your Mac (a real browser there passes reCAPTCHA far more often than a server). For every role the worker adds on a supported system, it:

1. **Reads the form's questions** from the system itself: Greenhouse's and Ashby's public job APIs, or the question templates embedded in Lever's apply page.
2. **Answers them.** Standard and legal questions (contact, work authorization, sponsorship, EEO, relocation) come from fixed rules over your profile. Everything else, including written answers, goes to Claude Haiku (`claude-haiku-5-5`) in one call, with both resumes, an answer bank of your approved answers, and the job description. Haiku flags anything it had to guess, and long written answers are always flagged for you to read.
3. **Fills the form** in a headless browser, so it never takes over your screen, and keeps the filled page open.
4. **Asks you in a separate Telegram chat** (its own bot, so approvals don't get lost among alerts), showing only the answers to check:
   - **✅ Submit** submits that same filled page. If Greenhouse emails a security code, the bot asks for it in the chat.
   - **✏️ Open & edit** opens the form, filled, in a visible window; change anything and submit there or from Telegram.
   - **🌐 Open in my Chrome** opens the posting in your normal Chrome (Simplify fills the standard fields) and sends the bot's answers ready to copy; tap **✅ I applied** when done. Lever's hCaptcha rejects automated browsers, so Lever roles use this route only.
   - **⏭ Skip.**
5. **Logs the application** to your tracker sheet (company, role, location, date, status, resume version, notes) through a small Apps Script web app.

You can also send the apply bot any job link to prepare that application, e.g. an older role or one from an Instagram story. Which resume is used depends on the title: AI, ML or data roles get the AI/ML version, the rest the SWE version.

| System | Roles (approx.) | Status |
|---|---|---|
| Greenhouse | 560 | Supported (incl. Greenhouse forms embedded on company sites) |
| Ashby | 310 | Supported |
| Lever | 135 | Supported, submitted from your own Chrome |
| Workday, Oracle, SmartRecruiters, others | 3,000+ | Not yet; skipped |

Your personal data (profile, resumes, answer bank, keys, browser profiles, application history) lives in `private/`, which is git-ignored.

## Answer writer

A separate Telegram bot (`apply/autoapply/writer.py`) for the written questions you answer yourself. Send it the company, role, job description and question in one message, in any format, and it replies with an answer in a copy box plus a short note on which story or project it used and what to double-check.

- **Revise** by replying to an answer ("shorter", "use a different project").
- **💾 Save** adds an answer to the answer bank, which the auto-applier reuses too.
- **Salary questions** trigger a web search for what that company pays interns, with sources.

It writes with Claude Haiku from your profile, both resumes, the answer bank and the files in `private/knowledge/`: a STAR story bank, prepared interview answers and project notes. These are re-read for every question, so edits apply right away. It runs on your Mac on its own, independent of the auto-apply bot.

## Repo layout

```
worker/       Python worker (scout package), schema.sql, tests
dashboard/    Next.js dashboard
apply/        Auto-apply bot and answer writer (autoapply package), tracker.gs for the sheet
private/      Your profile, resumes, knowledge files and keys (git-ignored, not in the repo)
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

### 5. Auto-apply (on your Mac)

```bash
cd apply
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

1. **Profile:** put `profile.yaml` (contact, education, work authorization, EEO answers, `facts` for company-specific questions) and your resume PDFs in `private/`. A file with "AI/ML" in its name is the AI/ML resume; the other is the SWE one. Optionally add `private/answer_bank.md` with answers you've approved.
2. **Tracker:** in your Google Sheet, open Extensions → Apps Script, paste `apply/tracker.gs`, add a `SECRET` script property, and deploy it as a web app (execute as you, access: anyone). The script only writes when the secret matches.
3. **Apply bot:** create a second bot with @BotFather and press Start in its chat.
4. **Keys:** in `private/tracker.env`:

| Variable | Value |
|---|---|
| `TRACKER_URL` | The web app URL (ends in `/exec`) |
| `TRACKER_SECRET` | The `SECRET` you set |
| `ANTHROPIC_API_KEY` | From console.anthropic.com |
| `APPLY_BOT_TOKEN` | The apply bot's token |
| `APPLY_CHAT_ID` | Your chat ID with the apply bot |
| `ANSWER_BOT_TOKEN` | The answer writer bot's token (a third bot from @BotFather) |
| `ANSWER_CHAT_ID` | Your chat ID with the answer writer bot |

`DATABASE_URL` is read from `worker/.env`.

```bash
python -m autoapply <job url>            # fill one form and screenshot it, no submit
python -m autoapply <job url> --submit   # fill, submit and log it
python -m autoapply.run                  # the bot: watch for new roles, ask in Telegram
python -m autoapply.writer               # the answer writer bot
```

For the answer writer, add `private/knowledge/*.md` (stories, prepared answers, project notes); any Markdown file there becomes context.

To keep the bots running, load each as a login agent (`launchctl bootstrap gui/$(id -u) <plist>`) with `caffeinate -i` so the Mac doesn't idle-sleep. It only applies while the Mac is awake and catches up on missed roles when it wakes.

## Cost

About $7–8/month: Railway ($5), Haiku for auto-apply (~$1.50–2) and the answer writer (under $1); everything else is on free plans. See [COSTS.md](COSTS.md) for the breakdown and a log of changes.

## Roadmap

- [x] Worker: polling, filters, dedup, Telegram alerts
- [x] Deploy on Railway + Supabase
- [x] Live dashboard
- [x] Auto-apply with Telegram approval: Greenhouse, Ashby, Lever
- [x] Tracker sheet logging
- [x] Answer writer bot for written application questions
- [ ] More application systems: SmartRecruiters, Workable, then Workday (needs an account per company)
- [ ] A Chrome extension that fills the bot's answers into your own browser
- [ ] Applying while the Mac is off (an always-on machine or a cloud browser)
- [ ] Instagram stories (zero2sudo): currently checked manually

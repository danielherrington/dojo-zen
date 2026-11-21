# 🎒 Dojo Zen — Agent Handoff Document (`HANDOFF.md`)

**Last Updated:** November 2026  
**Repository:** `danielherrington/dojo-zen` (`main` branch)  
**Production URL:** `https://dojo.herrington.ai` (Firebase Hosting `herrington-dojo.web.app` → Cloud Run `dojo-zen` in `us-central1`, GCP project `herrington-ai-site`)  
**Remote MCP Endpoint:** `https://dojo.herrington.ai/mcp` (Streamable HTTP; bearer token once `REQUIRE_AUTH=true`, see §7)  
**Linear Project:** **dojozen** ([`linear.app/herrington`](https://linear.app/herrington/project/classdojo-anti-bloat-agent-dab3a3988132) — `DAN-30` through `DAN-37`)

---

## 1. Executive Summary

**Dojo Zen** is an autonomous ClassDojo anti-bloat parental assistant built for Daniel & family (student: **Isabella Herrington**, Kindergarten, **Ms. Alonso's Class** at **North Beach Elementary School**).

It connects directly to ClassDojo's internal web REST APIs, strips out promotional upsells (*ClassDojo Plus*, *Dojo Beyond*, monster outfits), extracts actionable logistics (what to bring/wear, homework packets, test schedules, calendar events, and **Gemini Multimodal OCR** flyer transcripts), and surfaces them via:
1. **Daily Email Briefings** (7:00 AM ET via Cloud Scheduler + SMTP)
2. **Real-Time Emergency Alerts** (polled every 10 minutes via Cloud Scheduler; immediate high-priority email only for true emergencies)
3. **Mobile-Friendly Family Web Portal** (`https://dojo.herrington.ai`) with grounded Q&A, live briefing, and searchable unified feed
4. **Remote & Local Model Context Protocol (MCP) Server** (`https://dojo.herrington.ai/mcp` over Streamable HTTP + local `stdio`)
5. **Twilio SMS / WhatsApp Webhook** (`POST /api/sms/webhook`)

---

## 2. Live Cloud Architecture & Infrastructure

| Component | Resource / Identifier | Notes |
| :--- | :--- | :--- |
| **GCP Project** | `herrington-ai-site` (`647998166555`) | Primary Google Cloud & Firebase project |
| **Cloud Run Service** | `dojo-zen` (`us-central1`) | `https://dojo-zen-647998166555.us-central1.run.app` (512Mi RAM, 0–2 instances) |
| **Firebase Hosting** | Site `herrington-dojo` (target `dojo`) | Configured in [`firebase.json`](firebase.json) & [`.firebaserc`](.firebaserc); rewrites `**` and `/mcp/**` to `dojo-zen` |
| **Custom Domain & DNS** | `https://dojo.herrington.ai` | Cloudflare CNAME `dojo.herrington.ai -> ghs.googlehosted.com` (`proxied=false` DNS-only), managed via [`scripts/cloudflare_dns.py`](scripts/cloudflare_dns.py) |
| **Database (Prod)** | Cloud Firestore (`(default)`) | Enabled when `USE_FIRESTORE=true`. Collections: `dojo_children`, `dojo_classes`, `dojo_feed_items`, `dojo_messages`, `dojo_events`, `dojo_digests`, `dojo_alerts`, `dojo_email_sends`, `dojo_email_events`, `dojo_usage`, `dojo_config` |
| **Database (Local/Test)** | SQLite (`data/dojo.db`) | Default when `USE_FIRESTORE=false` |
| **Session Persistence** | Firestore `dojo_config/session` & `data/session.json` | Stores ClassDojo cookies (`dojo_login.sid`, `dojo_home_login.sid`) |
| **Cron: Urgent Alerts** | Cloud Scheduler `dojo-check-alerts` | `*/10 * * * *` (`America/New_York`) → `POST /api/cron/check-alerts` with `Authorization: Bearer $CRON_SECRET` |
| **Cron: Weekday Digest** | Cloud Scheduler `dojo-daily-digest` | `0 7 * * 1-5` (`America/New_York`, Mon–Fri 7:00 AM ET) → `POST /api/cron/daily-digest` with `Authorization: Bearer $CRON_SECRET` |
| **Cron: Sunday Digest** | Cloud Scheduler `dojo-sunday-digest` | `0 18 * * 0` (`America/New_York`, Sun 6:00 PM ET week-ahead; no Saturday or Sunday morning emails) → `POST /api/cron/daily-digest` with `Authorization: Bearer $CRON_SECRET` |

---

## 3. Codebase Map

```text
.
├── AGENTS.md                        # Mandatory workspace rules & guardrails
├── HANDOFF.md                       # This handoff guide for incoming agents
├── .agents/skills/                  # 9 portable skills checked into Git for Cloud Projects
│   ├── classdojo-api-and-sync/      # ClassDojo REST endpoints, Class Updates, Firestore sync
│   ├── dojo-digest-and-vision/      # Temporal contextualization, line-by-line parsing, Gemini OCR
│   ├── dojo-mcp-and-web-portal/     # FastAPI web portal, /mcp Streamable HTTP, Q&A, SMS
│   ├── dojo-cloud-deploy-and-dns/   # Cloud Run deploy, Firebase Hosting, Cloudflare DNS
│   ├── firebase-firestore/          # Firestore Standard/Enterprise SDK & indexing reference
│   ├── firebase-hosting-basics/     # Firebase Hosting rewrites & custom domain reference
│   ├── google-cloud-auth-verification/
│   ├── managing-python-dependencies/
│   └── uv/
├── firebase.json                    # Hosting rewrites (/mcp, /mcp/**, ** -> dojo-zen)
├── Dockerfile                       # Python 3.12 + uv production container image
├── scripts/
│   ├── deploy.sh                    # End-to-end Firestore seed + Cloud Run + Hosting + Scheduler deploy
│   └── cloudflare_dns.py            # Cloudflare API helper for herrington.ai DNS records
├── src/dojo/
│   ├── client.py                    # DojoClient REST client + sync_roster()
│   ├── config.py                    # Pydantic Settings (.env / environment variables)
│   ├── db.py                        # Local SQLite database (DojoDatabase) & get_database() factory
│   ├── firestore_db.py              # Production Cloud Firestore database (FirestoreDojoDatabase)
│   ├── digest.py                    # DigestEngine, temporal date resolver, attachment classifier
│   ├── vision.py                    # Gemini Multimodal OCR (gemini-2.5-flash) for flyer images
│   ├── qa.py                        # Grounded natural-language Q&A engine (DojoQA)
│   ├── alert_evaluator.py           # Urgency classifier (IMMEDIATE vs ROUTINE vs BLOAT)
│   ├── monitor.py                   # Real-time alert monitor (MessageMonitor)
│   ├── mailer.py                    # Jinja2 HTML + plain text SMTP dispatcher
│   ├── mcp_server.py                # MCP server (8 tools, 4 resources, 2 prompts)
│   ├── server.py                    # FastAPI app mounting Web UI, REST API, and /mcp
│   ├── sms.py                       # Twilio SMS/WhatsApp webhook handler
│   ├── cli.py                       # CLI entrypoints (login, sync, recap, run, serve, mcp, etc.)
│   ├── templates/                   # Jinja2 email templates (email_digest.html, email_alert.html)
│   └── web/index.html               # Mobile-friendly SPA (Ask Dojo, Today's Briefing, Class Feed)
└── tests/                           # 56 unit & integration tests (zero external credentials needed)
```

---

## 4. Key Technical Decisions & Recent Fixes (Context for Next Agent)

1. **ClassDojo REST API Endpoints ([`src/dojo/client.py`](src/dojo/client.py))**:
   - **Class Updates & Message Threads**: ClassDojo deprecated `/api/conversations` (which returns `[]`). [`DojoClient.get_messages()`](src/dojo/client.py) now queries `GET /api/parent/{parentId}/message-thread` and `GET /api/message-thread/{threadId}/message`.
     - Thread `type: "b"` is **Class Updates** (broadcast channel from teacher, e.g., `"Ms. Alonso — Class Updates (Ms. Alonso's Class)"`).
     - Thread `type: "a"` is Direct Teacher messages, `type: "e"` is Class Chat, and `type: "f"` is School Updates.
     - System join placeholders (`_id == "000000000000000000000000"`, `body == "You joined the class"`, or empty body without attachments) are filtered out automatically.
   - **School & Class Calendar Events**: ClassDojo deprecated `/api/events` (`404`). [`DojoClient.get_events()`](src/dojo/client.py) now queries `GET /api/calendar-event` and unpacks `contents.title`, `contents.description`, `start`, `end`, and `target.name`.
   - **Student & Classroom Roster**: Fetched from `GET /api/parent/{parentId}/student` via [`DojoClient.get_students()`](src/dojo/client.py) and persisted via [`sync_roster(client, db)`](src/dojo/client.py).
   - **Pinned Story Posts (`pinnedAt`)**: Story feed items with `pinnedAt` (such as Ms. Alonso's pinned *"Disguise a Turkey"* project) are automatically prefixed with `📌` in both SQLite and Firestore.

2. **Daily-Focused Horizon & Relative Temporal Contextualization ([`src/dojo/digest.py`](src/dojo/digest.py))**:
   - Daily briefings (`daily_focus=True`) follow an **Announce Once → Quiet → Tomorrow → Today → Expire** lifecycle:
     - Newly posted items (<24h & undigested) are announced once on the morning after they are posted.
     - Items with explicit target dates resurface on **Tomorrow** (`"Tomorrow (Weekday): ..."`) and **Today** (`"Today (Weekday): ..."`, `urgency="high"`), then expire immediately afterward (no "Past Notices" section).
     - Calendar events are included only when happening Today or Tomorrow; Teacher Messages and Classroom Highlights require `<24h` recency; quiet mornings skip sending email unless `force=True`.
   - [`evaluate_item_temporal_status(text, post_dt, ref_dt, context_text)`](src/dojo/digest.py) resolves relative time phrases against the post timestamp:
     - `"tomorrow"` posted yesterday becomes `"Today: ..."` with `urgency="high"`, and expires (`is_expired=True`, `is_transient=True`) the day after the event.
     - Multi-line posts (like weekly test schedules) are scanned line-by-line, passing `context_text=body` so weekday lines (`"Monday: ..."`, `"Wednesday: ..."`, `"Thursday: ..."`) inside a `"next week"` announcement resolve to **next week's** dates instead of matching the current week.

   - **Family timezone (DAN-165):** Cloud Run runs in UTC, so all wall-clock maths (Today/Tomorrow, <24h, Morning/Evening, weekend send window, Q&A recency) goes through [`src/dojo/tz.py`](src/dojo/tz.py) (`family_now()`, `to_family_tz()`), driven by `DIGEST_TIMEZONE` (default `America/New_York`). Never call bare `.astimezone()` or `datetime.now()` for family dates. `tzdata` is a dependency so `zoneinfo` works in the slim image.

3. **Attachment Classification ([`src/dojo/digest.py`](src/dojo/digest.py))**:
   - [`classify_attachment()`](src/dojo/digest.py) separates photos, videos (`.mp4`, `.mov`), and files (`.pdf`).
   - Videos use `thumbnailPath` (valid JPEG poster frame) for `image_urls` and put the `.mp4` URL in `video_urls` so `<img>` tags in emails and the Web Portal never break.

4. **Remote Streamable HTTP MCP Server ([`src/dojo/mcp_server.py`](src/dojo/mcp_server.py) & [`src/dojo/server.py`](src/dojo/server.py))**:
   - Mounted at `/mcp` using `mcp_server.streamable_http_app(streamable_http_path="/", transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))` with its session manager running inside FastAPI's `lifespan` context.
   - Shares the exact same `db` instance (`set_database(db)`) as the FastAPI app.

---

## 5. Working in an Antigravity Cloud Project (Step-by-Step)

When you open this repository in a fresh **Cloud Project** (where gitignored files `.env`, `.env.yaml`, `data/dojo.db`, and `data/session.json` are not on disk):

### Step 1: Install Dependencies & Run Tests
```bash
uv sync
uv run pytest
```
*(All 56 unit and HTTP MCP tests run against temporary SQLite databases and mock HTTP transports—no credentials required.)*

### Step 2: Verify GCP Authentication (`herrington-ai-site`)
```bash
gcloud auth list && gcloud config set project herrington-ai-site
```
If unauthenticated, prompt the user to run `gcloud auth login` and `gcloud auth application-default login`.

### Step 3: Read/Write Live Data & Session via Firestore
Because `DojoClient.load_session()` automatically loads session cookies from Firestore (`dojo_config/session`) when `USE_FIRESTORE=true`, you do **not** need a local `data/session.json` file in a Cloud Project:
```bash
USE_FIRESTORE=true GOOGLE_CLOUD_PROJECT=herrington-ai-site PYTHONPATH=src uv run python -c "
from dojo.client import DojoClient
from dojo.firestore_db import FirestoreDojoDatabase

db = FirestoreDojoDatabase(project='herrington-ai-site')
client = DojoClient()
print('Authenticated with ClassDojo:', client.is_authenticated())
print('Firestore stats:', db.get_stats())
"
```

### Step 4: Deploy to Cloud Run & Firebase Hosting
[`scripts/deploy.sh`](scripts/deploy.sh) automatically checks if `.env.yaml` exists locally; if not (as in a Cloud Project), `gcloud run deploy` preserves all existing environment variables (`USE_FIRESTORE`, `SMTP_*`, `GEMINI_API_KEY`, `CRON_SECRET`, etc.) already configured on the `dojo-zen` Cloud Run service:
```bash
gcloud run deploy dojo-zen \
  --source . \
  --region us-central1 \
  --project herrington-ai-site \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 2
```

### Step 5: Verify Production Health & MCP Endpoint
```bash
# 1. Check API & Firestore counts
curl -s https://dojo.herrington.ai/api/status | python3 -m json.tool

# 2. Trigger live ClassDojo -> Firestore sync on production
curl -s -X POST https://dojo.herrington.ai/api/sync | python3 -m json.tool
```

---

## 6. Troubleshooting Runbook

- **ClassDojo Session Expired (`401` on `/api/sync` or `client.is_authenticated() == False`)**:
  - ClassDojo sessions last several weeks/months, but if expired, ClassDojo requires an email One-Time Code (OTC).
  - Run `USE_FIRESTORE=true GOOGLE_CLOUD_PROJECT=herrington-ai-site uv run python -m dojo login --email daniel.j.herrington@gmail.com`, prompt Daniel for the 6-digit email code, and `DojoClient.save_session()` will automatically save the fresh cookies to Firestore (`dojo_config/session`).
- **Urgent Alert Failed to Send (`urgent_alerts_failed > 0` from `/api/cron/check-alerts`)**:
  - A failed IMMEDIATE alert stays in `alerts` / `dojo_alerts` with `sent_status="failed: ..."` and an `attempts` counter. [`MessageMonitor`](src/dojo/monitor.py) retries it on every later check (every 10 min in prod) until it is `sent`, it has been tried `MAX_ALERT_ATTEMPTS` (5) times, or the item is more than 24h old. Check SMTP credentials if failures persist.
- **Custom Domain (`dojo.herrington.ai`) DNS Issues**:
  - Check DNS record via `python3 scripts/cloudflare_dns.py list` (requires `CLOUDFLARE_API_TOKEN` or `~/.cloudflare/token`).
  - Ensure `dojo.herrington.ai` is a `CNAME` pointing to `ghs.googlehosted.com` with `proxied=false`.

---

## 7. Access Control (DAN-166)

[`src/dojo/auth.py`](src/dojo/auth.py) holds the token gate. It does nothing until `REQUIRE_AUTH=true` is set on Cloud Run, so merging never locks out clients.

| Surface | Auth once `REQUIRE_AUTH=true` |
| :--- | :--- |
| `/` (portal) | `__session` cookie set by `/login` (Firebase Hosting forwards only the cookie named `__session`); otherwise redirects to `/login` |
| `/api/*` data endpoints | `Authorization: Bearer $DOJO_ACCESS_TOKEN` or the `__session` cookie |
| `/mcp` | Bearer header or the cookie. Never `?access_token=`: URLs are stored in request logs (DAN-414) |
| `/api/status` | Public (counts only) |
| `/api/sms/webhook` | Valid `X-Twilio-Signature` (HMAC-SHA1 with `TWILIO_AUTH_TOKEN`); unsigned requests are logged and still allowed while `REQUIRE_AUTH` is off |
| `/t/o/*`, `/t/c/*` | Public email open pixel and click redirect (DAN-200): they take only random 32-hex email ids, store no IPs or user-agents, return no data, and redirect only to same-site paths |
| `/api/cron/*` | `CRON_SECRET` always; there's no built-in default, and an empty secret rejects every call |

**Where secrets live (rotated 2026-11-16):** `CRON_SECRET` and `DOJO_ACCESS_TOKEN` are env vars on the `dojo-zen` Cloud Run service. Copies are in Daniel's macOS Keychain (service `dojo-zen`, accounts `CRON_SECRET` / `DOJO_ACCESS_TOKEN`; read one with `security find-generic-password -s dojo-zen -a CRON_SECRET -w`). `CRON_SECRET` is also a GitHub Actions secret used by the scheduler-sync step. Never commit them. To rotate `CRON_SECRET`, change all three, then re-run the scheduler sync (`scripts/deploy.sh` or the workflow).

**Secret Manager (DAN-402 / DAN-414):** `DOJO_ACCESS_TOKEN` was rotated on 2026-11-20 and now lives only in Secret Manager secret `dojo-access-token`, mounted as an env var (`--update-secrets DOJO_ACCESS_TOKEN=dojo-access-token:latest`); the Keychain copy above is the old, revoked code. `CRON_SECRET`, `DOJO_PASSWORD`, `SMTP_PASS` and `GEMINI_API_KEY` are still plain env values on the service. `uv run python scripts/move_secrets_to_secret_manager.py` prints the plan (names only); `--apply` copies each value into secret `dojo-zen-<name>`, grants the runtime account access, and swaps all of them to `latest` secret references in one `gcloud run services replace`, so no revision runs without the access token. After that, rotate a secret with `gcloud secrets versions add` and a new revision; never `--env-vars-file`, which replaces every env var.

`DOJO_ACCESS_TOKEN` can hold several comma-separated tokens (for example one per family member or MCP client), so a single token can be revoked.

**Rollout:** (1) set `DOJO_ACCESS_TOKEN`, `TWILIO_AUTH_TOKEN` and `CRON_SECRET` on the `dojo-zen` service; (2) add the bearer header to MCP clients:
```json
{ "mcpServers": { "dojo-zen": { "url": "https://dojo.herrington.ai/mcp",
  "headers": { "Authorization": "Bearer <DOJO_ACCESS_TOKEN>" } } } }
```
(3) set `REQUIRE_AUTH=true`. Signing in once on each phone keeps email links working.


## 8. Email & Usage Analytics (DAN-200)

[`src/dojo/analytics.py`](src/dojo/analytics.py) adds best-effort analytics. A failure to record never blocks an email or a request.

- **Email opens and clicks.** Each briefing and alert sent through `Mailer(settings, db=...)` gets a random `email_id`. Portal links are rewritten to `/t/c/{id}?u=<path>`, and a 1×1 pixel `/t/o/{id}.gif` is appended. Each send is logged in `email_sends` / `dojo_email_sends`, and each open or click in `email_events` / `dojo_email_events`.
- **Usage patterns.** `UsageMiddleware` keeps one counter per channel per family-timezone day in `usage_daily` / `dojo_usage/{YYYY-MM-DD}`. The channels are `portal_view`, `api`, `ask`, `mcp` (POST calls) and `sms`. Requests rejected with a 4xx/5xx aren't counted.
- **Summary.** `GET /api/analytics?days=30` (token-gated under `/api/*`) returns open and click rates by kind, median minutes to first open, opens by hour, top click targets, and daily usage by channel.
- **Kill switches.** `EMAIL_TRACKING=false` and `USAGE_ANALYTICS=false`.
- **Limits.**
  - Apple Mail Privacy Protection inflates opens.
  - Gmail caches the pixel, so repeat opens are under-counted.
  - One message goes to every recipient, so an open means someone in the family opened it.
  - A click counts as an open even when images are blocked.

### Retention (DAN-205)

| Data | Collection / table | Kept for | Env var |
| :--- | :--- | :--- | :--- |
| Email sends (kind, subject, counts) | `dojo_email_sends` / `email_sends` | 90 days after `sent_at` | `ANALYTICS_RETENTION_DAYS` |
| Open/click events | `dojo_email_events` / `email_events` | 90 days after `created_at` | `ANALYTICS_RETENTION_DAYS` |
| Daily usage counters (aggregate only, no per-person data) | `dojo_usage` / `usage_daily` | 400 days after the day | `USAGE_RETENTION_DAYS` |

The counters are kept longer because `/api/analytics` reports up to 365 days of usage (busiest weekday, trends), and they contain only per-channel counts. Invalid or non-positive values fall back to the defaults.

- **Firestore (production):**
  - Every write sets an `expireAt` timestamp, and a Firestore TTL policy on each collection deletes the doc after that time, usually within 24h.
  - Reads hide rows past `expireAt` in the meantime.
  - The TTL policies have to be enabled once; see below.
- **SQLite (local):** there's no TTL, so `DojoDatabase.purge_expired_analytics()` runs on every send write and every analytics read.
- **Why TTL rather than a scheduled cleanup job:**
  - TTL needs no new Cloud Scheduler job, cron secret path or delete code running with production credentials.
  - Deletion happens inside Firestore, at no write cost.
  - Keeping data longer only means changing the env var for new rows.
  - The trade-offs:
    - Deletion can lag about 24h, which reads cover by filtering.
    - Changing the limit doesn't move `expireAt` on rows already written: the backfill only fills missing values. Lowering it later therefore applies to new rows only, and reads still use each row's own `expireAt`.
    - The TTL policy is a one-time console/gcloud step.
- **Backfill.** Rows written by DAN-200 before this change have no `expireAt`. `scripts/backfill_analytics_expiry.py` sets it from each row's own timestamp. It's a dry run by default; `--apply` writes.

**One-time setup, needs an owner of `herrington-ai-site`:**

```bash
uv run python scripts/backfill_analytics_expiry.py --apply
for c in dojo_email_sends dojo_email_events dojo_usage; do
  gcloud firestore fields ttls update expireAt --collection-group=$c --enable-ttl --project=herrington-ai-site --quiet
done
gcloud firestore fields ttls list --project=herrington-ai-site
```

### herrington.usage.v1 events, funnels and triage stats (DAN-233)

[`src/dojo/usage_v1.py`](src/dojo/usage_v1.py) maps DojoZen onto the Herrington analytics standard (`linear-conductor/docs/analytics-standard.md`).

- **Events.**
  - One JSON line per event on stdout, which Cloud Logging stores as `jsonPayload.schema="herrington.usage.v1"`, with `app=dojo-zen`.
  - `env` is `prod` on Cloud Run, `dev` locally and `test` under pytest; `ANALYTICS_ENV` overrides it.
  - Events emitted:
    - `feature_used`, per counted request: channel `web` / `mcp:<claude|cursor|…>` / `sms`, feature = DAN-200 channel.
    - `funnel_step` for the `alert` and `digest` funnels.
    - `triage`, channel `cron`.
- **Actor.** HMAC-SHA256(`ANALYTICS_SALT`, access token), first 16 hex. It's omitted when no salt is set. Email events never have an actor.
- **Funnels.**
  - `alert`: sent → opened → action_taken (clicked through to the flagged item).
  - `digest`: sent → opened → link_clicked.
  - A click implies an open.
  - The email kind travels in the tracking URLs (`k=alert|digest`), so open/click events need no database read.
  - `/api/analytics` and the report compute the funnels from the 90-day `email_sends` / `email_events` rows.
- **Triage.**
  - `AlertDecision.category`: health, logistics, teacher_request, school_closure, health_notice, routine, bloat.
  - `AlertDecision.guards`: negation, policy_reminder, paperwork.
  - `MessageMonitor` counts each item's first evaluation as `triage__<category>` and `guard__<guard>` on `dojo_usage/{day}`, plus `guard__prevented_alert` when guards stopped every match.
- **Report.** `uv run dojo analytics report --days 7`. Add `USE_FIRESTORE=true` for production data; it only reads.
- **Not yet built:** the BigQuery log sink (infra, needs Daniel's approval), `error` / `zero_result` / `duration_ms` events, and per-MCP-tool feature use.

## 9. School & Family Logistics MCP (DAN-251)

[`src/dojo/logistics.py`](src/dojo/logistics.py) backs three read-only tools on the existing MCP server: `school_today`, `school_upcoming` and `pickup_owner`.

- **Family Schedule Helper (DAN-396):** the default source for pickups.
  - Dojo Zen reads the helper's Firestore doc `family/setup` in project `family-schedule-helper`. It's read-only: the runtime account has `roles/datastore.viewer` there and nothing else. The read is cached for 10 minutes.
  - On by default when `USE_FIRESTORE=true`. `FAMILY_SETUP_PROJECT` overrides the project; `off` disables it.
  - Schools are not taken from it: its children list includes the dog without `isPet`, and its schools disagree with the ClassDojo roster, so `FAMILY_SCHOOLS` stays the source for schools.
  - `pickup_owner` answers from the weekly plan: each pickup/drop-off's default caregiver, rerouted by travel cover (`covering_for`). Locations and notes are never returned. One-day swaps made on Google Calendar aren't visible yet, and the response says so.
- **Children and schools: `FAMILY_SCHOOLS`** (JSON list of `{child, school, source}`).
  - With it unset, the Dojo roster's first names are used.
  - Only `source: "dojo"` is connected. Others, such as `lehrman`, return `status: "not_connected"`.
- **Pickup ownership override: `FAMILY_PICKUP_CALENDARS`** (JSON `{owner: secret iCal URL}`, one per Family calendar). When set, it wins over the helper's weekly plan.
  - Recurring events are expanded with `recurring-ical-events`.
  - An event counts if its title matches pickup/carpool/dismissal or drop-off.
  - The owner is the calendar the instance sits on.
  - Feeds are cached for 10 minutes. These URLs are secrets: store them in Secret Manager, never in the repo.
- **Privacy:**
  - The tools return titles, dates, times and configured child labels only. Descriptions, message bodies, sender names and attachments are never returned.
  - `pickup_owner` refuses on Cloud Run unless `REQUIRE_AUTH=true`.
- **Catalog:** [`src/dojo/mcp_catalog.json`](src/dojo/mcp_catalog.json) is served at `/.well-known/mcp-catalog.json`, with `last_health_check` taken from the last sync.

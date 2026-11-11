# 🎒 Dojo Zen — Agent Handoff Document (`HANDOFF.md`)

**Last Updated:** November 2026  
**Repository:** `danielherrington/dojo-zen` (`main` branch)  
**Production URL:** `https://dojo.herrington.ai` (Firebase Hosting `herrington-dojo.web.app` → Cloud Run `dojo-zen` in `us-central1`, GCP project `herrington-ai-site`)  
**Remote MCP Endpoint:** `https://dojo.herrington.ai/mcp` (Streamable HTTP, unauthenticated)  
**Linear Project:** [ClassDojo Anti-Bloat Agent](https://linear.app/herrington/project/classdojo-anti-bloat-agent-dab3a3988132) (`DAN-30` through `DAN-34` — all completed)

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
| **Database (Prod)** | Cloud Firestore (`(default)`) | Enabled when `USE_FIRESTORE=true`. Collections: `dojo_children`, `dojo_classes`, `dojo_feed_items`, `dojo_messages`, `dojo_events`, `dojo_digests`, `dojo_alerts`, `dojo_config` |
| **Database (Local/Test)** | SQLite (`data/dojo.db`) | Default when `USE_FIRESTORE=false` |
| **Session Persistence** | Firestore `dojo_config/session` & `data/session.json` | Stores ClassDojo cookies (`dojo_login.sid`, `dojo_home_login.sid`) |
| **Cron: Urgent Alerts** | Cloud Scheduler `dojo-check-alerts` | `*/10 * * * *` (`America/New_York`) → `POST /api/cron/check-alerts?secret=...` |
| **Cron: Daily Digest** | Cloud Scheduler `dojo-daily-digest` | `0 7 * * *` (`America/New_York`) → `POST /api/cron/daily-digest?secret=...` |

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
└── tests/                           # 53 unit & integration tests (zero external credentials needed)
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

2. **Relative Temporal Contextualization ([`src/dojo/digest.py`](src/dojo/digest.py))**:
   - [`evaluate_item_temporal_status(text, post_dt, ref_dt, context_text)`](src/dojo/digest.py) resolves relative time phrases against the post timestamp:
     - `"tomorrow"` posted yesterday becomes `"Today: ..."` with `urgency="high"`, and expires (`is_expired=True`, `is_transient=True`) the day after the event so it disappears from both active and past notices.
     - Multi-line posts (like weekly test schedules) are scanned line-by-line, passing `context_text=body` so weekday lines (`"Monday: ..."`, `"Wednesday: ..."`, `"Thursday: ..."`) inside a `"next week"` announcement resolve to **next week's** dates instead of matching the current week.

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
*(All 53 unit and HTTP MCP tests run against temporary SQLite databases and mock HTTP transports—no credentials required.)*

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
- **Custom Domain (`dojo.herrington.ai`) DNS Issues**:
  - Check DNS record via `python3 scripts/cloudflare_dns.py list` (requires `CLOUDFLARE_API_TOKEN` or `~/.cloudflare/token`).
  - Ensure `dojo.herrington.ai` is a `CNAME` pointing to `ghs.googlehosted.com` with `proxied=false`.

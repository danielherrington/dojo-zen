# 🎒 Dojo Zen — ClassDojo Anti-Bloat Agent, Web Portal & MCP Server

An autonomous parental assistant that monitors ClassDojo via direct REST API, strips out the app bloat (marketing popups, monster avatars, gamification noise), extracts flyer text with Gemini Multimodal OCR, and delivers high-signal school logistics via email briefings, a mobile-friendly web portal, and a remote Model Context Protocol (MCP) server.

---

## ⚡ Features

- **Direct REST API Client**: Connects directly to `home.classdojo.com/api/session` along with story feeds, Class Updates message threads, and school calendar endpoints with session cookie persistence.
- **Two-Factor (OTC) & Browser Fallback**: Gracefully handles ClassDojo's email One-Time Code verification, with an optional Playwright helper (`dojo login --browser`) for interactive CAPTCHAs.
- **Daily-Focused Anti-Bloat Digest Engine**: Automatically filters out promotional spam (*ClassDojo Plus*, *Dojo Beyond*, monster clothing upsells), skips sending emails on quiet days, and focuses strictly on what matters today:
  - 🚨 **Today's Action Items & To-Dos**: Newly posted (<24h) logistics plus items due **Today** or **Tomorrow** (automatically resurfacing reminders posted earlier in the week).
  - 📅 **Today & Tomorrow's Schedule**: Calendar events and school reminders happening today or tomorrow, plus newly announced dates.
  - 💬 **New Teacher Messages (Last 24h)**: Direct notes and pinned classroom broadcasts sent within the past 24 hours.
  - 🌟 **Today's Classroom Highlights**: Clean summaries of new (<24h) classroom activities without stale or historical feed noise.
- **Gemini Multimodal Flyer OCR**: Scans attached school flyers and images (`dojo analyze-images`) to extract embedded dates and action items.
- **Intelligent Real-Time Alerting**: Analyzes incoming messages so emergencies (illness/nurse visits, same-day bus delays, urgent calls requested) trigger an immediate priority email while routine posts wait for the morning recap.
- **Family Web Portal & Q&A Assistant**: Mobile-friendly FastAPI portal (`dojo serve`, live at `https://dojo.herrington.ai`) with grounded natural-language Q&A and Twilio SMS/WhatsApp webhook support (`/api/sms/webhook`).
- **Model Context Protocol (MCP) Server**: Exposes classroom announcements, action items, upcoming dates, and grounded Q&A over both remote Streamable HTTP (`/mcp`) and local `stdio` (`dojo mcp`).
- **Dual Storage Parity**: Uses local SQLite (`data/dojo.db`) for CLI/local development and Google Cloud Firestore (`USE_FIRESTORE=true`) in Cloud Run production.

---

## 🚀 Quick Start

### 1. Setup Environment
```bash
# Install dependencies using uv
uv sync
```

### 2. Configure Credentials
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Edit `.env` with your details:
```env
# ClassDojo Account
DOJO_EMAIL=your-email@example.com
DOJO_PASSWORD=your-password

# Optional: Gemini API Key (for Multimodal Flyer OCR & LLM Q&A synthesis)
GEMINI_API_KEY=your-gemini-api-key

# Email Dispatcher (e.g. Gmail with an App Password)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASS=your-16-char-app-password
SMTP_USE_TLS=true
EMAIL_TO=your-inbox@example.com
```

### 3. Log In to ClassDojo
```bash
# Direct API Login (will prompt for 6-digit OTC code if 2FA is active)
uv run dojo login

# Or if you encounter a CAPTCHA:
uv run dojo login --browser
```

### 4. Sync & Generate Your First Briefing
```bash
# Check current system status
uv run dojo status

# Fetch new stories, Class Updates, messages, and events
uv run dojo sync

# Preview your briefing in the terminal and generate HTML preview
uv run dojo recap --dry-run --force

# Send the briefing to your inbox
uv run dojo recap --send-email
```

---

## 🛠️ CLI Reference

| Command | Description |
| :--- | :--- |
| `dojo login` | Authenticate directly to ClassDojo API and save session cookies |
| `dojo login --browser` | Launch Playwright browser window to log in interactively and save cookies |
| `dojo sync [--limit 50]` | Fetch latest stories, Class Updates, messages, and events |
| `dojo analyze-images [--limit 10]` | Scan feed image attachments & flyers using Gemini Multimodal OCR |
| `dojo recap` | Synthesize un-digested items into a clean briefing |
| `dojo recap --dry-run` | Preview recap without marking items as digested |
| `dojo recap --send-email` | Dispatch briefing to your email inbox and mark items digested |
| `dojo run` | One-shot command: Syncs from ClassDojo and emails digest if new items exist |
| `dojo check-alerts` | One-shot check: Evaluates newly arrived messages and sends immediate priority alerts if urgent |
| `dojo watch [--interval 300]` | Continuous monitoring daemon polling ClassDojo in real-time |
| `dojo serve [--port 8000]` | Launch the family web portal, Q&A assistant, and HTTP `/mcp` server |
| `dojo mcp [--transport stdio]` | Run the Model Context Protocol (MCP) server over `stdio` or `sse` |
| `dojo seed-firestore` | Migrate local SQLite data and session cookies to Google Cloud Firestore |
| `dojo status` | View session health, student counts, and database stats |
| `dojo analytics report [--days 7] [--json]` | Analyst report: daily activity by channel, alert/digest funnels, feature use, triage categories and false-alarm guards |

---

## 📊 What We Collect (Analytics)

DojoZen follows the herrington.usage.v1 analytics standard (DAN-200, DAN-205, DAN-233). It collects **aggregates and random ids only**:

- **Email funnels.** A random 32-hex id per briefing or alert lets us count sent → opened → clicked. Opens come from a 1×1 pixel and clicks from a same-site redirect. No addresses, subjects or IPs are stored, and opens are labelled only `gmail_proxy` / `other`.
- **Usage.** Daily request counts per channel: portal, API, Ask, MCP, SMS.
- **Alert triage.** Daily counts of which triage category fired (health, logistics, …) and which false-alarm guard (negation, policy reminder, paperwork) suppressed a keyword. **Message text is never stored or logged.**
- **v1 log events.** One JSON line per event in Cloud Logging. `actor` is a salted hash of the access token (omitted without `ANALYTICS_SALT`); email events have no actor.
- **Retention.** Per-event rows: 90 days. Daily counters: 400 days. Both are enforced by Firestore TTL.
- **Off switch.** `ANALYTICS=off` disables all of it. `EMAIL_TRACKING=false` and `USAGE_ANALYTICS=false` turn off one part each.

## 🔌 Model Context Protocol (MCP) Server

Dojo Zen exposes **8 tools** (`get_daily_briefing`, `get_action_items`, `get_upcoming_dates`, `ask_classroom_assistant`, `search_feed`, `get_recent_feed`, `get_feed_item`, `get_enrolled_children`), **4 resources** (`dojo://briefing/today`, `dojo://action-items`, `dojo://calendar/upcoming`, `dojo://feed/recent`), and **2 prompts** (`weekly-school-prep`, `urgent-logistics-check`).

**School & Family Logistics (DAN-251)** adds 3 read-only tools:
- `school_today(date?)`: per-child events and action items due today or tomorrow.
- `school_upcoming(days=14)`: days off, early dismissals, deadlines and events.
- `pickup_owner(date?, kind?)`: who has pickup or drop-off. The owner is the Family calendar the event instance sits on.

Children, schools and calendar feeds come from env (`FAMILY_SCHOOLS`, `FAMILY_PICKUP_CALENDARS`), never code. `pickup_owner` refuses on Cloud Run until `REQUIRE_AUTH=true`. Tool-catalog metadata is at `/.well-known/mcp-catalog.json` (`src/dojo/mcp_catalog.json`).

- **Remote Streamable HTTP**: Connect any MCP client to `https://dojo.herrington.ai/mcp` (or `http://localhost:8000/mcp` when running `dojo serve`). When `REQUIRE_AUTH=true`, send `Authorization: Bearer <DOJO_ACCESS_TOKEN>` (see HANDOFF.md §7).
- **Local `stdio`**:
  ```json
  {
    "mcpServers": {
      "dojo-zen": {
        "command": "uv",
        "args": ["--directory", "/path/to/dojo-zen", "run", "dojo", "mcp"]
      }
    }
  }
  ```

---

## 📋 Linear Project Tracking

Tracked in Linear Project: **dojozen** ([`linear.app/herrington`](https://linear.app/herrington/project/classdojo-anti-bloat-agent-dab3a3988132))

- [x] **[DAN-30]** Direct REST API Client & SQLite Ingestion
- [x] **[DAN-31]** Anti-Bloat Daily Digest & Email Dispatcher
- [x] **[DAN-32]** Real-Time Message Monitor & Intelligent Urgency Alerting
- [x] **[DAN-33]** Web Portal & Natural Language Q&A Assistant for ClassDojo (Mobile-Friendly)
- [x] **[DAN-34]** Model Context Protocol (MCP) Server for ClassDojo (Remote Streamable HTTP `/mcp` & Local Stdio)
- [x] **[DAN-37]** Gemini Multimodal OCR for Image Attachments & Unblurred Feed Viewer

---

## ⏰ Automated Daily Scheduling

In production on Google Cloud Run (`dojo-zen`), Cloud Scheduler triggers `/api/cron/daily-digest` on **Sunday evenings at 6:00 PM ET** (`0 18 * * 0` — first briefing for the new school week) and **weekday mornings at 7:00 AM ET** (`0 7 * * 1-5` — Mon–Fri), skipping Saturday and Sunday morning completely. To run locally via cron on the same schedule:

```bash
# Open crontab editor
crontab -e

# Run Sunday at 6:00 PM (week-ahead briefing) and Mon-Fri at 7:00 AM
0 18 * * 0 cd /path/to/dojo-zen && uv run dojo run >> /tmp/dojo.log 2>&1
0 7 * * 1-5 cd /path/to/dojo-zen && uv run dojo run >> /tmp/dojo.log 2>&1
```

---

## 🗄️ Storage Schema (SQLite & Cloud Firestore)

Data is stored locally in `data/dojo.db` (SQLite) or in Google Cloud Firestore when `USE_FIRESTORE=true`:

- **`children` / `dojo_children`**: `id`, `name`, `grade`, `avatar_url`, `updated_at`
- **`classes` / `dojo_classes`**: `id`, `name`, `teacher_name`, `child_id`, `updated_at`
- **`feed_items` / `dojo_feed_items`**: `id`, `story_id`, `class_id`, `author_name`, `content_text`, `attachments_json`, `ocr_json`, `item_timestamp`, `digested_at`
- **`messages` / `dojo_messages`**: `id`, `conversation_id`, `sender_name`, `body`, `message_timestamp`, `digested_at`
- **`events` / `dojo_events`**: `id`, `title`, `description`, `start_time`, `end_time`, `digested_at`
- **`digests` / `dojo_digests`**: `id`, `created_at`, `content_html`, `content_text`, `recipient_email`, `sent_status`
- **`email_sends` / `dojo_email_sends`**, **`email_events` / `dojo_email_events`**, **`usage_daily` / `dojo_usage`**: Email open/click analytics and per-channel daily usage counters, summarised at `GET /api/analytics` (DAN-200, see HANDOFF.md §8). Per-event rows expire after 90 days and daily counters after 400 (DAN-205; Firestore TTL on `expireAt`).
- **`alerts` / `dojo_alerts`** & **`dojo_config/session`** (Firestore): Urgent alert audit log and persisted ClassDojo session cookies.

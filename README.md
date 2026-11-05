# 🎒 Dojo Zen — ClassDojo Anti-Bloat Agent, Web Portal & MCP Server

An autonomous parental assistant that monitors ClassDojo via direct REST API, strips out the app bloat (marketing popups, monster avatars, gamification noise), extracts flyer text with Gemini Multimodal OCR, and delivers high-signal school logistics via email briefings, a mobile-friendly web portal, and a remote Model Context Protocol (MCP) server.

---

## ⚡ Features

- **Direct REST API Client**: Connects directly to `home.classdojo.com/api/session` along with story feeds, Class Updates message threads, and school calendar endpoints with session cookie persistence.
- **Two-Factor (OTC) & Browser Fallback**: Gracefully handles ClassDojo's email One-Time Code verification, with an optional Playwright helper (`dojo login --browser`) for interactive CAPTCHAs.
- **Anti-Bloat Digest Engine**: Automatically filters out promotional spam (*ClassDojo Plus*, *Dojo Beyond*, monster clothing upsells) and isolates:
  - 🚨 **Action Items & To-Dos**: What to bring, what to wear, permission slips, homework deadlines.
  - 📅 **Upcoming Dates & Schedule**: Early dismissals, holidays, field trips, spirit days.
  - 💬 **Teacher Messages & Class Updates**: Direct notes and pinned classroom broadcasts from teachers.
  - 🌟 **Classroom Highlights**: Clean summaries of school activities without endless photo noise.
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

---

## 🔌 Model Context Protocol (MCP) Server

Dojo Zen exposes **8 tools** (`get_daily_briefing`, `get_action_items`, `get_upcoming_dates`, `ask_classroom_assistant`, `search_feed`, `get_recent_feed`, `get_feed_item`, `get_enrolled_children`), **4 resources** (`dojo://briefing/today`, `dojo://action-items`, `dojo://calendar/upcoming`, `dojo://feed/recent`), and **2 prompts** (`weekly-school-prep`, `urgent-logistics-check`).

- **Remote Streamable HTTP**: Connect any MCP client to `https://dojo.herrington.ai/mcp` (or `http://localhost:8000/mcp` when running `dojo serve`).
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

Tracked in Linear Project: [**ClassDojo Anti-Bloat Agent**](https://linear.app/herrington/project/classdojo-anti-bloat-agent-dab3a3988132)

- [x] **[DAN-30]** Direct REST API Client & SQLite Ingestion
- [x] **[DAN-31]** Anti-Bloat Daily Digest & Email Dispatcher
- [x] **[DAN-32]** Real-Time Message Monitor & Intelligent Urgency Alerting
- [x] **[DAN-33]** Web Portal & Natural Language Q&A Assistant for ClassDojo (Mobile-Friendly)
- [x] **[DAN-34]** Model Context Protocol (MCP) Server for ClassDojo (Remote Streamable HTTP `/mcp` & Local Stdio)

---

## ⏰ Automated Daily Scheduling

In production on Google Cloud Run (`dojo-zen`), Cloud Scheduler triggers `/api/cron/sync` and `/api/cron/daily-briefing` automatically. To run locally via cron on a weekday schedule:

```bash
# Open crontab editor
crontab -e

# Run every weekday at 7:00 AM
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
- **`alerts` / `dojo_alerts`** & **`dojo_config/session`** (Firestore): Urgent alert audit log and persisted ClassDojo session cookies.

# 🎒 ClassDojo Anti-Bloat Agent & Daily Briefing

An autonomous agent that monitors ClassDojo via direct REST API, strips out the app bloat (marketing popups, monster avatars, gamification noise), stores posts, messages, and events in a local SQLite database, and delivers a clean, high-signal briefing directly to your email inbox.

---

## ⚡ Features

- **Direct REST API Client**: Connects directly to `home.classdojo.com/api/session` and story/message feeds with session cookie persistence.
- **Two-Factor (OTC) Ready**: Gracefully handles ClassDojo's One-Time Code email verification.
- **Interactive Browser Fallback**: Optional Playwright helper (`dojo login --browser`) if ClassDojo ever triggers interactive CAPTCHAs or Cloudflare verification.
- **Anti-Bloat Digest Engine**: Automatically filters out promotional spam (*ClassDojo Plus*, *Dojo Beyond*, monster clothing upsells) and isolates:
  - 🚨 **Action Items & To-Dos**: What to bring, what to wear, permission slips, homework deadlines.
  - 📅 **Upcoming Dates & Schedule**: Early dismissals, holidays, field trips, spirit days.
  - 💬 **Teacher Messages**: Direct notes from classroom teachers.
  - 🌟 **Classroom Highlights**: Clean summaries of school activities without endless photo noise.
- **Intelligent Real-Time Alerting**: A background monitor analyzes every message as it arrives. Emergencies (illness/nurse visits, same-day bus delays, urgent calls requested) dispatch an immediate high-priority alert email, while routine announcements are held for the daily morning recap.
- **Email Delivery**: Sends mobile-optimized HTML + plain text email briefings and emergency alerts via standard SMTP (Gmail App Password, iCloud, etc.).
- **Local SQLite Store**: Retains historical records in `data/dojo.db`, ready for future Telegram bots, local search, or MCP servers.

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
uv run python -m dojo login

# Or if you encounter a CAPTCHA:
uv run python -m dojo login --browser
```

### 4. Sync & Generate Your First Briefing
```bash
# Check current system status
uv run python -m dojo status

# Fetch new stories, messages, and events into SQLite
uv run python -m dojo sync

# Preview your briefing in the terminal and generate HTML preview
uv run python -m dojo recap --dry-run --force

# Send the briefing to your inbox
uv run python -m dojo recap --send-email
```

---

## 🛠️ CLI Reference

| Command | Description |
| :--- | :--- |
| `dojo login` | Authenticate directly to ClassDojo API and save session cookies |
| `dojo login --browser` | Launch Playwright browser window to log in interactively and save cookies |
| `dojo sync [--limit 50]` | Fetch latest stories, messages, and events into SQLite |
| `dojo recap` | Synthesize un-digested items into a clean briefing |
| `dojo recap --dry-run` | Preview recap without marking items as digested in SQLite |
| `dojo recap --send-email` | Dispatch briefing to your email inbox and mark items digested |
| `dojo run` | One-shot command: Syncs from ClassDojo and emails digest if new items exist |
| `dojo check-alerts` | One-shot check: Evaluates newly arrived messages and sends immediate priority alerts if urgent |
| `dojo watch [--interval 300]` | Continuous monitoring daemon polling ClassDojo in real-time |
| `dojo serve [--port 8000]` | Launch the family web portal & Q&A assistant (accessible on phones/tablets) |
| `dojo status` | View session health, student counts, and database stats |

---

## 📋 Linear Project Tracking

Tracked in Linear Project: [**ClassDojo Anti-Bloat Agent**](https://linear.app/herrington/project/classdojo-anti-bloat-agent-dab3a3988132)

- [x] **[DAN-30]** Direct REST API Client & SQLite Ingestion
- [x] **[DAN-31]** Anti-Bloat Daily Digest & Email Dispatcher
- [x] **[DAN-32]** Real-Time Message Monitor & Intelligent Urgency Alerting
- [x] **[DAN-33]** Web Portal & Natural Language Q&A Assistant for ClassDojo (Mobile-Friendly)
- [ ] **[DAN-34]** Model Context Protocol (MCP) Server for ClassDojo

---

## ⏰ Automated Daily Scheduling

To receive a briefing every morning (e.g. 7:00 AM) automatically, add a cron job:

```bash
# Open crontab editor
crontab -e

# Run every weekday at 7:00 AM
0 7 * * 1-5 cd /path/to/serene-turing && uv run python -m dojo run >> /tmp/dojo.log 2>&1
```

---

## 🗄️ Database Schema (`data/dojo.db`)

All data is stored in SQLite for durability and future integration:

- **`children`**: `id`, `name`, `grade`, `avatar_url`, `updated_at`
- **`classes`**: `id`, `name`, `teacher_name`, `child_id`, `updated_at`
- **`feed_items`**: `id`, `story_id`, `class_id`, `author_name`, `content_text`, `attachments_json`, `item_timestamp`, `digested_at`
- **`messages`**: `id`, `conversation_id`, `sender_name`, `body`, `message_timestamp`, `digested_at`
- **`events`**: `id`, `title`, `description`, `start_time`, `end_time`, `digested_at`
- **`digests`**: `id`, `created_at`, `content_html`, `content_text`, `recipient_email`, `sent_status`

---

## 🔮 Future Extensions

Because all data is structured in SQLite, you can easily build on top of this foundation:
1. **Telegram Bot**: Query upcoming events or receive instant notifications when a teacher messages.
2. **MCP Server**: Connect this SQLite database as a Model Context Protocol tool so any AI assistant can answer *"What does Leo need for school tomorrow?"*.

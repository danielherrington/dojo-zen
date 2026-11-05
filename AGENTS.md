# Dojo Zen — Agent Rules & Cloud Project Context (`AGENTS.md`)

This repository powers **Dojo Zen** (`danielherrington/dojo-zen`), an autonomous ClassDojo anti-bloat parental assistant, Daily Briefing & Emergency Alert engine, Gemini Multimodal OCR flyer scanner, mobile-friendly Web Portal, and remote Streamable HTTP MCP Server deployed at `https://dojo.herrington.ai`.

---

## 1. Non-Negotiable Architecture & Guardrails

1. **Backwards-Compatible Public MCP Surface (`/mcp` & `stdio`)**:
   - Never break the remote Streamable HTTP MCP server mounted at `/mcp` in [`src/dojo/server.py`](src/dojo/server.py) or local `stdio` transport in [`src/dojo/mcp_server.py`](src/dojo/mcp_server.py).
   - Keep all 7 MCP tools (`get_daily_briefing`, `get_action_items`, `get_upcoming_dates`, `ask_classroom_assistant`, `search_feed`, `get_recent_feed`, `get_feed_item`, `get_enrolled_children`), 4 MCP resources (`dojo://briefing/today`, `dojo://action-items`, `dojo://calendar/upcoming`, `dojo://feed/recent`), and 2 MCP prompts (`weekly-school-prep`, `urgent-logistics-check`) intact.
2. **Dual Storage Parity (Cloud Firestore in Production + SQLite Locally)**:
   - Production Cloud Run (`USE_FIRESTORE=true`, GCP project `herrington-ai-site`) reads and writes exclusively to **Google Cloud Firestore** (`dojo_children`, `dojo_classes`, `dojo_feed_items`, `dojo_messages`, `dojo_events`, `dojo_digests`, `dojo_alerts`, and `dojo_config/session`) via [`src/dojo/firestore_db.py`](src/dojo/firestore_db.py).
   - Local CLI & unit tests default to SQLite (`data/dojo.db`) via [`src/dojo/db.py`](src/dojo/db.py).
   - Whenever adding a new field or query method, update **both** [`DojoDatabase`](src/dojo/db.py) and [`FirestoreDojoDatabase`](src/dojo/firestore_db.py).
3. **ClassDojo Session & 2FA OTC Protection**:
   - ClassDojo enforces email One-Time Code (OTC) 2FA on fresh logins (`ERR_MUST_USE_OTC_USER_OPTED_IN`).
   - Session cookies (`dojo_login.sid`, `dojo_home_login.sid`) are persisted in Firestore (`dojo_config/session`) and locally in `data/session.json`.
   - [`DojoClient`](src/dojo/client.py) automatically loads session cookies from Firestore when `USE_FIRESTORE=true` or when `data/session.json` is absent in a Cloud Project. **Never** trigger automated password login loops in cron jobs if `client.is_authenticated()` returns `False`.
4. **Firebase Hosting & Cloudflare DNS Protection (`dojo.herrington.ai`)**:
   - Hosted via Firebase Hosting target `dojo` (`herrington-dojo.web.app`) rewriting all traffic (`**` and `/mcp/**`) to Cloud Run service `dojo-zen` (`us-central1`, project `herrington-ai-site`).
   - Custom domain `dojo.herrington.ai` uses a Cloudflare DNS-only (`proxied=false`) CNAME pointing to `ghs.googlehosted.com`, managed via [`scripts/cloudflare_dns.py`](scripts/cloudflare_dns.py).

---

## 2. Cloud / Remote Agent Environment Notes

When working inside an **Antigravity Cloud Project** cloned from GitHub:
- **Gitignored Local State (`data/`, `.env`, `.env.yaml`)**:
  - Local `data/dojo.db` and `data/session.json` are excluded from Git for security.
  - In a Cloud Project with GCP Application Default Credentials (`herrington-ai-site`), set `USE_FIRESTORE=true` and `GOOGLE_CLOUD_PROJECT=herrington-ai-site` to read/write live classroom data and load the active ClassDojo session directly from Firestore (`dojo_config/session`).
- **Python Dependency Management (`uv`)**:
  - Always use `uv sync` and `uv run ...` (never global `pip install`).
- **Testing & Verification**:
  - Run full unit & HTTP MCP test suite (53 tests, zero external credentials needed):
    ```bash
    uv run pytest
    ```
  - Verify live production health & MCP endpoint:
    ```bash
    curl -s https://dojo.herrington.ai/api/status | python3 -m json.tool
    ```

---

## 3. Workspace Skills (`.agents/skills/`)

All specialized skills used to build, operate, and deploy Dojo Zen are checked into [`.agents/skills/`](.agents/skills/) so they are automatically available in any Cloud Project:

- [`classdojo-api-and-sync`](.agents/skills/classdojo-api-and-sync/SKILL.md) — ClassDojo internal REST API endpoints (`/api/storyFeed`, `/api/parent/{parentId}/message-thread`, `/api/message-thread/{threadId}/message` for **Class Updates**, `/api/calendar-event`, `/api/parent/{parentId}/student`, `/api/award`), `pinnedAt` handling, and Firestore/SQLite sync workflows.
- [`dojo-digest-and-vision`](.agents/skills/dojo-digest-and-vision/SKILL.md) — Anti-bloat filtering, relative temporal contextualization (`tomorrow` → `Today`, `"next week"` weekday resolution, transient reminder expiration), video/PDF attachment classification, and Gemini Multimodal OCR flyer extraction.
- [`dojo-mcp-and-web-portal`](.agents/skills/dojo-mcp-and-web-portal/SKILL.md) — FastAPI Web Portal (`src/dojo/server.py`), Streamable HTTP MCP Server mounting (`/mcp`), grounded Q&A (`src/dojo/qa.py`), and Twilio SMS/WhatsApp webhook integration.
- [`dojo-cloud-deploy-and-dns`](.agents/skills/dojo-cloud-deploy-and-dns/SKILL.md) — Cloud Run deployment (`dojo-zen` in `us-central1`), Firebase Hosting multi-site rewrites (`herrington-dojo`), Cloud Scheduler cron jobs, and Cloudflare DNS automation (`scripts/cloudflare_dns.py`).
- [`managing-python-dependencies`](.agents/skills/managing-python-dependencies/SKILL.md) — Enforces `uv` dependency management (`uv sync`, `uv add`, `uv run`).
- [`google-cloud-auth-verification`](.agents/skills/google-cloud-auth-verification/SKILL.md) — Pre-flight GCP & Application Default Credentials verification for Cloud Run and Firestore.
- [`firebase-firestore`](.agents/skills/firebase-firestore/SKILL.md) — Cloud Firestore database operations, collections, and Python SDK patterns.
- [`firebase-hosting-basics`](.agents/skills/firebase-hosting-basics/SKILL.md) — Firebase Hosting configuration, Cloud Run rewrites, and custom domain setup.

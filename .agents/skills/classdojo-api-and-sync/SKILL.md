---
name: classdojo-api-and-sync
description: >-
  Guides reverse-engineered ClassDojo REST API integration, session cookie management,
  Class Updates (message-thread) ingestion, school calendar events, student roster sync,
  and SQLite/Firestore persistence. Use when modifying DojoClient (src/dojo/client.py),
  debugging missing ClassDojo posts/messages/events, or syncing data to Firestore.
---

# ClassDojo Internal REST API & Data Sync Skill

## Overview

ClassDojo's parent web app (`https://home.classdojo.com`) uses internal REST endpoints authenticated via session cookies (`dojo_login.sid`, `dojo_home_login.sid`). Because ClassDojo frequently deprecates legacy v1 endpoints (returning empty `[]` or `404`), always use the verified endpoints documented below.

## Verified ClassDojo REST Endpoints (`https://home.classdojo.com`)

| Data Type | Active Endpoint | Notes & Payload Structure |
| :--- | :--- | :--- |
| **Session & Parent ID** | `GET /api/session` | Returns `parent._id` (e.g. `6a7baaf56fc72fac92728e66`). Does **not** include children directly. |
| **Students & Classes** | `GET /api/parent/{parentId}/student` | Returns `_items[]` with student `_id`, `firstName`, `lastName`, `year` (`0` = Kindergarten), `schoolId`, and `classes[]` (each with `_id`, `name`, and `teacher`). |
| **Story Feed (Posts)** | `GET /api/storyFeed?includePrivate=true` | Returns `_items[]` with `_id`, `time`, `senderName`, `headerSubtext`, `pinnedAt`, and `contents` (`body`, `attachments[]`). Paginate via `_links.prev.href`. |
| **Message Threads (Class Updates & Chats)** | `GET /api/parent/{parentId}/message-thread` | Lists all threads in `_items[]`. Thread `type` values: `"b"` = **Class Updates** (broadcast channel from teacher), `"a"` = **Direct Teacher Thread**, `"e"` = **Class Chat**, `"f"` = **School Updates**. (Legacy `/api/conversations` returns `[]` — do not use.) |
| **Messages in Thread** | `GET /api/message-thread/{threadId}/message` | Returns `_items[]` with `_id`, `body`, `createdAt`, `attachments[]`, and `sender` (`_id`, `formattedName`, `firstName`, `lastName`, `title`). Skip system join placeholders where `_id == "000000000000000000000000"` or `body == "You joined the class"` or empty body without attachments. |
| **School & Class Calendar Events** | `GET /api/calendar-event` | Returns `_items[]` with `_id`, `start`, `end`, `contents.title`, `contents.description`, `target` (`_id`, `name`, `type`), and `hosts[]`. (Legacy `/api/events` returns `404` — do not use.) |
| **Student Awards / Points** | `GET /api/award?studentId={studentId}` | Returns `_items[]` with `dateAwarded`, `name` (e.g. `"Working hard"`, `"Awesome Recall"`), `weight`, and `teacher`. |
| **Unread Counts** | `GET /api/parent/{parentId}/unreads` | Returns `{"count": 0, "messagesCount": 0, "storiesCount": 0}`. |

## Pinned Posts (`pinnedAt`)

When a teacher pins a story post on ClassDojo, the feed item in `/api/storyFeed` includes:
```json
"pinnedAt": "2026-10-28T14:51:59.761Z",
"pinnedBy": {"_id": "...", "title": "Ms.", "firstName": "Tatiana", "lastName": "Alonso"}
```
Both [`DojoDatabase.upsert_feed_items()`](../../src/dojo/db.py) and [`FirestoreDojoDatabase.upsert_feed_items()`](../../src/dojo/firestore_db.py) detect `pinnedAt` and prefix the author/header with `📌` (`📌 Ms. Alonso (Ms. Alonso's Class) (Pinned)`).

## Discovering New Endpoints from the Web Bundle

If ClassDojo adds a new UI tab or changes an endpoint, scan the live production JS chunks:
```bash
PYTHONPATH=src uv run python -c '
import re
from concurrent.futures import ThreadPoolExecutor
from dojo.config import settings
from dojo.client import DojoClient, BASE_URL

client = DojoClient(session_file=settings.dojo_session_file)
resp = client.client.get(BASE_URL)
entry = re.findall(r"src=\"(/assets/index-[^\"]+\.js)\"", resp.text)[0]
js = client.client.get(f"{BASE_URL}{entry}").text
chunks = sorted(set(re.findall(r"[a-zA-Z0-9_-]+\.js", js)))
apis = set(re.findall(r"/api/[a-zA-Z0-9_/{}$?=&.-]+", js))
with ThreadPoolExecutor(max_workers=20) as ex:
    for r in ex.map(lambda c: set(re.findall(r"/api/[a-zA-Z0-9_/{}$?=&.-]+", client.client.get(f"{BASE_URL}/assets/{c}").text)), chunks):
        apis.update(r)
for a in sorted(apis):
    print(a)
'
```

## Syncing Data in Cloud vs. Local Environments

1. **Sync to Local SQLite**:
   ```bash
   PYTHONPATH=src uv run python -m dojo sync --limit 50
   ```
2. **Seed / Sync to Production Cloud Firestore (`herrington-ai-site`)**:
   ```bash
   PYTHONPATH=src uv run python -m dojo seed-firestore
   ```
3. **Sync Directly in a Cloud Project (without local `data/session.json`)**:
   ```bash
   USE_FIRESTORE=true GOOGLE_CLOUD_PROJECT=herrington-ai-site PYTHONPATH=src uv run python -c "
   from dojo.client import DojoClient, sync_roster
   from dojo.firestore_db import FirestoreDojoDatabase
   fdb = FirestoreDojoDatabase(project='herrington-ai-site')
   client = DojoClient()
   sync_roster(client, fdb)
   fdb.upsert_feed_items(client.get_story_feed(limit=50))
   fdb.upsert_messages(client.get_messages())
   fdb.upsert_events(client.get_events())
   "
   ```

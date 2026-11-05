---
name: dojo-digest-and-vision
description: >-
  Guides the Dojo Zen DigestEngine (src/dojo/digest.py) and Gemini Multimodal OCR
  scanner (src/dojo/vision.py): anti-bloat filtering, relative date contextualization
  ('tomorrow' -> 'Today', 'next week' weekday resolution, expiring past reminders),
  video/file attachment classification, and flyer text extraction. Use when modifying
  briefing synthesis, date/action extraction, email templates, or image OCR.
---

# Anti-Bloat Digest Engine & Multimodal Vision Skill

## Overview

[`DigestEngine`](../../src/dojo/digest.py) transforms raw ClassDojo feed posts, Class Updates / teacher messages, and calendar events into a structured `Briefing` containing:
- `active_action_items`: Current parental to-dos (what to bring, wear, homework, permission slips).
- `active_upcoming_dates`: Upcoming tests, holidays, early dismissals, project due dates, and calendar events.
- `teacher_notes`: Recent (`<= 7` days) undigested Class Updates and direct teacher messages.
- `classroom_highlights`: Recent undigested classroom photos/videos with attachment counts.
- `expired_action_items`: Non-transient historical notices from earlier in the week.

## Key Rules in `src/dojo/digest.py`

### 1. Temporal Contextualization (`evaluate_item_temporal_status`)
Teachers write relative dates (`"tomorrow"`, `"tonight"`, `"on Monday"`, `"next week"`). [`evaluate_item_temporal_status(text, post_dt, ref_dt, context_text)`](../../src/dojo/digest.py) resolves them against the post's timestamp (`post_dt`) and current time (`ref_dt`):
- **`"tomorrow"` / `"tmrw"`**:
  - `target_date = post_date + 1 day`
  - If `target_date == ref_date`: Rewrites summary to `"Today: ..."` (stripping `"tomorrow"`) and sets `urgency="high"`.
  - If `target_date < ref_date`: Marks `is_expired=True` and `is_transient=True` so single-day reminders (like *"wear green tomorrow"*) disappear completely the day after the event.
- **Weekdays (`Monday`–`Sunday`) & `"Next Week"` Context**:
  - Multi-line posts (such as Class Updates listing `"Next week's tests:\nMonday: Sight word review test\nWednesday: Chapter 5 math test\nThursday: Spelling test"`) pass `context_text=body` into `evaluate_item_temporal_status`.
  - When `"next week"` appears in `text` or `context_text`, weekdays resolve to **next week's** Monday–Sunday (`days_to_next_monday + target_weekday`) rather than matching the current week's day.
  - Excludes homework packet labels like `"Monday and Tuesday pages"` via negative lookahead `(?!(?:\s+and\s+\w+)?\s+(?:pages?|packets?|sheets?|worksheets?|folders?))`.

### 2. Multi-Line Schedule & Bullet Scanning
Both `messages` (Class Updates) and `feed_items` split multi-line bodies on `\n` and evaluate each line against `DATE_KEYWORDS` and `ACTION_KEYWORDS` so every test date or due date in a bulleted post is captured individually, followed by deduplication (`_deduplicate_actions`, `_deduplicate_dates`).

### 3. Video, Photo, and PDF Attachment Classification (`classify_attachment`)
ClassDojo attachments include photos (`.jpg`, `.webp`, `.png`), videos (`.mp4`, `.mov`), and files (`.pdf`):
- **Never** put raw `.mp4` or `.pdf` URLs into `image_urls` (which renders broken `<img>` tags in email clients and the web UI).
- For videos (`type == "video"` or `.mp4`), [`classify_attachment()`](../../src/dojo/digest.py) puts `thumbnailPath` (the valid JPEG poster frame) into `image_urls` and the `.mp4` stream URL into `video_urls`.
- Already-digested items (`digested_at is not None`) strip `image_urls` so recurring action items don't re-email old photos.

### 4. Gemini Multimodal OCR (`src/dojo/vision.py`)
Teachers frequently post flyers as images without typing the dates in the post body.
- [`analyze_feed_attachments()`](../../src/dojo/vision.py) downloads image attachments with ClassDojo cookies and calls Gemini (`gemini-2.5-flash`) to extract `has_text`, `summary`, `full_text`, `dates[]`, and `action_items[]`.
- Results are stored on the feed item (`ocr_json` in SQLite, `ocr_data` in Firestore) and automatically merged into `Briefing` (`_integrate_ocr_into_briefing`) and searchable in [`DojoQA`](../../src/dojo/qa.py).
- CLI command to scan un-analyzed feed images:
  ```bash
  PYTHONPATH=src uv run python -m dojo analyze-images --limit 10
  ```

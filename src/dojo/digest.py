"""Anti-bloat digest and summarization engine for ClassDojo content."""

import re
from datetime import datetime, timezone, date, timedelta
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field, computed_field

# Patterns indicative of ClassDojo marketing, avatar dressing, or gamification bloat
BLOAT_PATTERNS = [
    r"classdojo plus",
    r"dojo beyond",
    r"try classdojo plus",
    r"unlock all monster",
    r"dress your monster",
    r"monster outfit",
    r"start your free trial",
    r"get unlimited points at home",
    r"subscribe now",
    r"upgrade your account",
]

ACTION_KEYWORDS = [
    "bring", "wear", "sneakers", "shoes", "pack", "permission slip",
    "due", "homework", "submit", "don't forget", "reminder", "please return",
    "by friday", "tomorrow", "bring in", "dollar", "$", "sign and return",
    "project due", "library books", "snack", "jacket", "sight words", "practice"
]

DATE_KEYWORDS = [
    "early dismissal", "early release", "no school", "holiday", "closed",
    "field trip", "picture day", "spirit week", "conference", "assembly",
    "book fair", "parade", "open house", "half day", "starts at", "ends at", "weekend",
    "test schedule", "math test", "spelling test", "sight word review test", "no tests",
    "due date", "due on", "due monday", "due friday", "teacher planning day"
]

WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6
}

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12
}


def parse_raw_datetime(raw_time: Any) -> Optional[datetime]:
    """Parse raw timestamp (ISO string, datetime, or date) into a timezone-aware datetime."""
    if not raw_time:
        return None
    if isinstance(raw_time, datetime):
        return raw_time.astimezone() if raw_time.tzinfo else raw_time.replace(tzinfo=timezone.utc).astimezone()
    if isinstance(raw_time, date):
        return datetime(raw_time.year, raw_time.month, raw_time.day).astimezone()
    s = str(raw_time).strip()
    try:
        clean = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        return dt.astimezone()
    except Exception:
        pass
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).astimezone()
        except Exception:
            pass
    return None


def parse_date_or_datetime(val: Any, default_year: Optional[int] = None) -> Optional[date]:
    """Parse string, datetime, or date into a date object."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    fallback_year = default_year or datetime.now().year
    try:
        clean = s.replace("Z", "+00:00")
        return datetime.fromisoformat(clean).date()
    except Exception:
        pass
    match_iso = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if match_iso:
        try:
            return date(int(match_iso.group(1)), int(match_iso.group(2)), int(match_iso.group(3)))
        except Exception:
            pass
    date_match = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b",
        s,
        re.IGNORECASE,
    )
    if date_match:
        m_str = date_match.group(1)[:3].lower()
        m_num = MONTHS.get(m_str)
        d_num = int(date_match.group(2))
        y_num = int(date_match.group(3)) if date_match.group(3) else fallback_year
        try:
            return date(y_num, m_num, d_num)
        except Exception:
            pass
    slash_match = re.search(r"(?:^|\b(?:on|by|due|for|date:?)\s+)(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", s, re.IGNORECASE)
    if slash_match:
        m_num = int(slash_match.group(1))
        d_num = int(slash_match.group(2))
        raw_y = slash_match.group(3)
        y_num = (2000 + int(raw_y) if len(raw_y) == 2 else int(raw_y)) if raw_y else fallback_year
        try:
            return date(y_num, m_num, d_num)
        except Exception:
            pass
    return None


def is_posted_within_24h(post_dt: Optional[datetime], ref_dt: Optional[datetime] = None) -> bool:
    """Return True if post_dt is on the same local date as ref_dt or within the preceding 24 hours."""
    if ref_dt is None:
        ref_dt = datetime.now().astimezone()
    if post_dt is None:
        return True
    if post_dt.date() == ref_dt.date():
        return True
    delta_seconds = (ref_dt - post_dt).total_seconds()
    return 0 <= delta_seconds <= 86400


def collect_briefing_inputs(db: Any, feed_limit: int = 50, msg_limit: int = 30) -> Dict[str, Any]:
    """Gather undigested items merged with recent items so Today/Tomorrow reminders from earlier days are evaluated."""
    undigested = (
        db.get_undigested_items()
        if hasattr(db, "get_undigested_items")
        else {"feed_items": [], "messages": [], "events": []}
    )
    recent_feed = db.get_all_feed_items(limit=feed_limit) if hasattr(db, "get_all_feed_items") else []
    recent_msgs = db.get_all_messages(limit=msg_limit) if hasattr(db, "get_all_messages") else []
    all_events = db.get_all_events() if hasattr(db, "get_all_events") else []

    def _merge_by_id(primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        merged = []
        for row in primary + secondary:
            rid = str(row.get("id") or "")
            if rid:
                if rid in seen:
                    continue
                seen.add(rid)
            merged.append(row)
        return merged

    return {
        "undigested": undigested,
        "feed_items": _merge_by_id(undigested.get("feed_items", []), recent_feed),
        "messages": _merge_by_id(undigested.get("messages", []), recent_msgs),
        "events": _merge_by_id(undigested.get("events", []), all_events),
        "children": db.get_all_children() if hasattr(db, "get_all_children") else [],
    }


def evaluate_item_temporal_status(
    text: str,
    post_dt: Optional[datetime],
    ref_dt: Optional[datetime] = None,
    context_text: Optional[str] = None,
    daily_focus: bool = True,
) -> Dict[str, Any]:
    """
    Evaluates relative time expressions in text relative to post_dt and ref_dt.
    Detects tomorrow, tonight, today, weekends, days of week, explicit dates, and undated posts.
    """
    if ref_dt is None:
        ref_dt = datetime.now().astimezone()
    if post_dt is None:
        post_dt = ref_dt

    post_date = post_dt.date()
    ref_date = ref_dt.date()
    tomorrow_date = ref_date + timedelta(days=1)
    days_since_post = (ref_date - post_date).days
    lower = text.lower()
    lower_ctx = (context_text or text).lower()

    # 1. "Tomorrow" / "tmrw"
    if re.search(r"\b(tomorrow|tmrw)\b", lower):
        target_date = post_date + timedelta(days=1)
        if target_date < ref_date:
            return {
                "is_expired": True,
                "is_today": False,
                "is_tomorrow": False,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": target_date,
                "contextualized_summary": text,
                "urgency": "expired",
            }
        elif target_date == ref_date:
            clean = text.strip()
            # If text has e.g. "💚 Don't forget to wear GREEN tomorrow", strip "tomorrow" and prefix "Today: "
            sub_text = re.sub(r"\s+\b(?:tomorrow|tmrw)\b\.?", "", clean, flags=re.IGNORECASE)
            sub_text = re.sub(r"\btomorrow\s+is\b", "Today is", sub_text, flags=re.IGNORECASE)
            sub_text = re.sub(r"\btmrw\s+is\b", "Today is", sub_text, flags=re.IGNORECASE)
            if not sub_text.lower().startswith("today") and not sub_text.lower().startswith("reminder: today"):
                rewritten = f"Today: {sub_text}"
            else:
                rewritten = sub_text
            return {
                "is_expired": False,
                "is_today": True,
                "is_tomorrow": False,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": target_date,
                "contextualized_summary": rewritten,
                "urgency": "high",
            }
        else:
            rewritten = text if text.lower().startswith("tomorrow") else f"Tomorrow: {text}"
            return {
                "is_expired": False,
                "is_today": False,
                "is_tomorrow": target_date == tomorrow_date,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": target_date,
                "contextualized_summary": rewritten,
                "urgency": "normal",
            }

    # 2. "Tonight", "today", "this morning", "this afternoon", "this evening"
    same_day_match = re.search(r"\b(tonight|today|this morning|this afternoon|this evening)\b", lower)
    if same_day_match:
        target_date = post_date
        if target_date < ref_date:
            return {
                "is_expired": True,
                "is_today": False,
                "is_tomorrow": False,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": target_date,
                "contextualized_summary": text,
                "urgency": "expired",
            }
        elif target_date == ref_date:
            return {
                "is_expired": False,
                "is_today": True,
                "is_tomorrow": False,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": target_date,
                "contextualized_summary": text,
                "urgency": "high",
            }
        else:
            return {
                "is_expired": False,
                "is_today": False,
                "is_tomorrow": target_date == tomorrow_date,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": target_date,
                "contextualized_summary": text,
                "urgency": "normal",
            }

    # 3. Explicit Calendar Dates (e.g. "Sep 18", "September 21st", "on 9/21")
    explicit_target: Optional[date] = None
    date_match = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b",
        lower,
    )
    if date_match:
        month_name = date_match.group(1)[:3].lower()
        month_num = MONTHS.get(month_name, 1)
        day_num = int(date_match.group(2))
        year_num = int(date_match.group(3)) if date_match.group(3) else (
            post_date.year + 1 if post_date.month >= 11 and month_num <= 2 else post_date.year
        )
        try:
            explicit_target = date(year_num, month_num, day_num)
        except ValueError:
            explicit_target = None
    else:
        slash_match = re.search(r"(?:^|\b(?:on|by|due|for|date:?)\s+)(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", lower)
        if slash_match:
            month_num = int(slash_match.group(1))
            day_num = int(slash_match.group(2))
            raw_y = slash_match.group(3)
            year_num = (
                (2000 + int(raw_y) if len(raw_y) == 2 else int(raw_y))
                if raw_y
                else (post_date.year + 1 if post_date.month >= 11 and month_num <= 2 else post_date.year)
            )
            try:
                explicit_target = date(year_num, month_num, day_num)
            except ValueError:
                explicit_target = None

    if explicit_target is not None:
        if explicit_target < ref_date:
            return {
                "is_expired": True,
                "is_today": False,
                "is_tomorrow": False,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": explicit_target,
                "contextualized_summary": text,
                "urgency": "expired",
            }
        elif explicit_target == ref_date:
            rewritten = text if text.lower().startswith("today") else f"Today: {text}"
            return {
                "is_expired": False,
                "is_today": True,
                "is_tomorrow": False,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": explicit_target,
                "contextualized_summary": rewritten,
                "urgency": "high",
            }
        elif explicit_target == tomorrow_date:
            rewritten = text if re.search(r"\b(tomorrow|tmrw)\b", lower) else f"Tomorrow: {text}"
            return {
                "is_expired": False,
                "is_today": False,
                "is_tomorrow": True,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": explicit_target,
                "contextualized_summary": rewritten,
                "urgency": "normal",
            }
        else:
            return {
                "is_expired": False,
                "is_today": False,
                "is_tomorrow": False,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": explicit_target,
                "contextualized_summary": text,
                "urgency": "normal",
            }

    # 4. "This weekend", "the weekend"
    if "this weekend" in lower or "the weekend" in lower:
        if post_date.weekday() == 6:
            target_saturday = post_date
            target_sunday = post_date
        else:
            target_saturday = post_date + timedelta(days=(5 - post_date.weekday()) % 7)
            target_sunday = target_saturday + timedelta(days=1)
        if ref_date > target_sunday:
            return {
                "is_expired": True,
                "is_today": False,
                "is_tomorrow": False,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": target_sunday,
                "contextualized_summary": text,
                "urgency": "expired",
            }
        elif target_saturday <= ref_date <= target_sunday:
            return {
                "is_expired": False,
                "is_today": True,
                "is_tomorrow": False,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": target_sunday,
                "contextualized_summary": text,
                "urgency": "high",
            }
        elif tomorrow_date == target_saturday:
            return {
                "is_expired": False,
                "is_today": False,
                "is_tomorrow": True,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": target_saturday,
                "contextualized_summary": text,
                "urgency": "normal",
            }
        else:
            return {
                "is_expired": False,
                "is_today": False,
                "is_tomorrow": False,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": target_sunday,
                "contextualized_summary": text,
                "urgency": "normal",
            }

    # 5. Day of the week (Monday - Sunday)
    weekday_match = re.search(
        r"\b(?:on\s+|this\s+|next\s+|by\s+|due\s+|is\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?!(?:\s+and\s+\w+)?\s+(?:pages?|packets?|sheets?|worksheets?|folders?))\b",
        lower,
    )
    if weekday_match:
        target_day_name = weekday_match.group(1).lower()
        target_weekday = WEEKDAYS[target_day_name]
        post_weekday = post_date.weekday()

        if "next week" in lower or "next week" in lower_ctx:
            days_to_next_monday = (7 - post_weekday) % 7 or 7
            day_diff = days_to_next_monday + target_weekday
        else:
            day_diff = (target_weekday - post_weekday) % 7
            if "next" in lower and day_diff == 0:
                day_diff = 7

        target_date = post_date + timedelta(days=day_diff)
        if target_date < ref_date:
            return {
                "is_expired": True,
                "is_today": False,
                "is_tomorrow": False,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": target_date,
                "contextualized_summary": text,
                "urgency": "expired",
            }
        elif target_date == ref_date:
            rewritten = text if text.lower().startswith("today") else f"Today ({target_day_name.capitalize()}): {text}"
            return {
                "is_expired": False,
                "is_today": True,
                "is_tomorrow": False,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": target_date,
                "contextualized_summary": rewritten,
                "urgency": "high",
            }
        elif target_date == tomorrow_date:
            rewritten = text if re.search(r"\b(tomorrow|tmrw)\b", lower) else f"Tomorrow ({target_day_name.capitalize()}): {text}"
            return {
                "is_expired": False,
                "is_today": False,
                "is_tomorrow": True,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": target_date,
                "contextualized_summary": rewritten,
                "urgency": "normal",
            }
        else:
            return {
                "is_expired": False,
                "is_today": False,
                "is_tomorrow": False,
                "is_transient": True,
                "has_explicit_date": True,
                "target_date": target_date,
                "contextualized_summary": text,
                "urgency": "normal",
            }

    # 6. Fallback for undated posts:
    if daily_focus:
        if not is_posted_within_24h(post_dt, ref_dt):
            return {
                "is_expired": True,
                "is_today": False,
                "is_tomorrow": False,
                "is_transient": True,
                "has_explicit_date": False,
                "target_date": post_date,
                "contextualized_summary": text,
                "urgency": "expired",
            }
        return {
            "is_expired": False,
            "is_today": True,
            "is_tomorrow": False,
            "is_transient": False,
            "has_explicit_date": False,
            "target_date": post_date,
            "contextualized_summary": text,
            "urgency": "normal",
        }

    if days_since_post >= 3:
        return {
            "is_expired": True,
            "is_today": False,
            "is_tomorrow": False,
            "is_transient": False,
            "has_explicit_date": False,
            "target_date": post_date,
            "contextualized_summary": text,
            "urgency": "expired",
        }

    return {
        "is_expired": False,
        "is_today": days_since_post == 0,
        "is_tomorrow": False,
        "is_transient": False,
        "has_explicit_date": False,
        "target_date": post_date,
        "contextualized_summary": text,
        "urgency": "normal",
    }


def parse_and_format_timestamp(raw_time: Optional[str], ref_dt: Optional[datetime] = None) -> Tuple[str, bool]:
    """
    Parses an ISO timestamp and returns:
    - Human-readable local display string, e.g.:
        "Today at 11:20 AM", "Yesterday at 3:15 PM", "Sep 10 (4d ago)", "Aug 28 (17d ago)"
    - Boolean is_older_than_48h indicating whether the post is older than 48 hours.
    """
    if not raw_time:
        return ("Recently", False)

    try:
        clean_time = raw_time.replace("Z", "+00:00")
        utc_dt = datetime.fromisoformat(clean_time)
        # Convert UTC to local timezone
        local_dt = utc_dt.astimezone()
        now = ref_dt.astimezone() if ref_dt is not None else datetime.now().astimezone()
        delta = now - local_dt

        # Handle future timestamps or small clock skews
        if delta.total_seconds() < 0:
            if abs(delta.total_seconds()) < 300:
                return ("Just now", False)
            # Future date or event
            time_str = local_dt.strftime("%I:%M %p").lstrip("0")
            if local_dt.date() == now.date():
                return (f"Today at {time_str}", False)
            days_ahead = (local_dt.date() - now.date()).days
            return (f"{local_dt.strftime('%b %d')} (in {days_ahead}d)", False)

        time_str = local_dt.strftime("%I:%M %p").lstrip("0")

        # Check calendar date in local timezone
        if local_dt.date() == now.date():
            if delta.total_seconds() < 3600:
                mins = max(1, int(delta.total_seconds() // 60))
                return (f"{mins}m ago ({time_str})", False)
            return (f"Today at {time_str}", False)
        elif (now.date() - local_dt.date()).days == 1:
            return (f"Yesterday at {time_str}", False)
        else:
            days_ago = (now.date() - local_dt.date()).days
            date_str = local_dt.strftime("%b %d")
            return (f"{date_str} ({days_ago}d ago)", days_ago >= 2)
    except Exception:
        return (str(raw_time)[:10], False)


def classify_attachment(att: Any) -> Dict[str, Any]:
    """
    Classifies a ClassDojo attachment into photo, video, or file.
    Extracts displayable image URL (poster frame for video, thumbnail or image for photo, None for raw video/file).
    """
    if not isinstance(att, dict):
        return {"kind": "unknown", "image_url": None, "media_url": None, "thumbnail_url": None, "filename": "Attachment"}

    att_type = str(att.get("type") or "").strip().lower()
    path = str(att.get("path") or att.get("url") or "").strip()
    path_lower = path.lower()
    thumb = str(att.get("thumbnailPath") or att.get("thumbnailUrl") or att.get("poster") or "").strip() or None

    is_video = (
        att_type == "video"
        or any(ext in path_lower for ext in [".mp4", ".mov", ".webm", ".m4v"])
    )
    is_pdf = (
        att_type == "file"
        or any(ext in path_lower for ext in [".pdf", ".doc", ".docx", ".xls", ".xlsx"])
    )

    if is_video:
        return {
            "kind": "video",
            "image_url": thumb,  # Valid JPEG poster frame suitable for <img> tags
            "media_url": path if path else None,   # .mp4 stream URL for <video> player or links
            "thumbnail_url": thumb,
            "filename": att.get("metadata", {}).get("filename") if isinstance(att.get("metadata"), dict) else "Video",
        }
    elif is_pdf:
        return {
            "kind": "file",
            "image_url": thumb,  # Only if a thumbnail image was generated, never the raw .pdf
            "media_url": path if path else None,
            "thumbnail_url": thumb,
            "filename": att.get("metadata", {}).get("filename") if isinstance(att.get("metadata"), dict) else "Document",
        }
    else:
        return {
            "kind": "photo",
            "image_url": path if path else None,
            "media_url": path if path else None,
            "thumbnail_url": thumb or (path if path else None),
            "filename": att.get("metadata", {}).get("filename") if isinstance(att.get("metadata"), dict) else "Photo",
        }


class ActionItem(BaseModel):
    summary: str
    context: str
    author: Optional[str] = None
    urgency: str = "normal"  # "high", "normal", "expired"
    posted_at_str: str = "Recently"
    is_stale: bool = False
    is_transient: bool = False
    image_urls: List[str] = Field(default_factory=list)
    video_urls: List[str] = Field(default_factory=list)
    item_id: Optional[str] = None


class UpcomingDate(BaseModel):
    title: str
    date_str: str
    details: Optional[str] = None
    posted_at_str: Optional[str] = None
    author: Optional[str] = None
    image_urls: List[str] = Field(default_factory=list)
    video_urls: List[str] = Field(default_factory=list)
    item_id: Optional[str] = None
    is_stale: bool = False


class TeacherNote(BaseModel):
    sender: str
    body: str
    timestamp: Optional[str] = None
    posted_at_str: str = "Recently"
    item_id: Optional[str] = None


class ClassroomHighlight(BaseModel):
    author: str
    text: str
    attachment_count: int = 0
    photo_count: int = 0
    video_count: int = 0
    file_count: int = 0
    image_urls: List[str] = Field(default_factory=list)
    video_urls: List[str] = Field(default_factory=list)
    file_urls: List[str] = Field(default_factory=list)
    date_str: Optional[str] = None
    posted_at_str: str = "Recently"
    item_id: Optional[str] = None

    @computed_field
    @property
    def attachment_label(self) -> str:
        parts = []
        if self.photo_count > 0:
            parts.append(f"📷 {self.photo_count} photo{'s' if self.photo_count > 1 else ''}")
        if self.video_count > 0:
            parts.append(f"🎥 {self.video_count} video{'s' if self.video_count > 1 else ''}")
        if self.file_count > 0:
            parts.append(f"📎 {self.file_count} file{'s' if self.file_count > 1 else ''}")
        if not parts and self.attachment_count > 0:
            parts.append(f"📷 {self.attachment_count} photo{'s' if self.attachment_count > 1 else ''}")
        return ", ".join(parts)


class Briefing(BaseModel):
    generated_at: str = Field(default_factory=lambda: datetime.now().astimezone().strftime("%A, %b %d, %Y"))
    period: str = Field(default_factory=lambda: "Morning" if datetime.now().astimezone().hour < 12 else "Evening")
    children_names: List[str] = Field(default_factory=list)
    action_items: List[ActionItem] = Field(default_factory=list)
    upcoming_dates: List[UpcomingDate] = Field(default_factory=list)
    teacher_notes: List[TeacherNote] = Field(default_factory=list)
    classroom_highlights: List[ClassroomHighlight] = Field(default_factory=list)
    raw_item_count: int = 0
    filtered_bloat_count: int = 0

    @property
    def active_action_items(self) -> List[ActionItem]:
        """Returns action items that are current and have not expired."""
        return [a for a in self.action_items if not a.is_stale]

    @property
    def expired_action_items(self) -> List[ActionItem]:
        """Returns older historical action items where the date has already passed, omitting transient single-day noise."""
        return [a for a in self.action_items if a.is_stale and not a.is_transient]

    @property
    def active_upcoming_dates(self) -> List[UpcomingDate]:
        """Returns upcoming dates that have not already passed."""
        return [d for d in self.upcoming_dates if not d.is_stale]

    @property
    def daily_item_count(self) -> int:
        """Count of active, daily-relevant items across all sections."""
        return (
            len(self.active_action_items)
            + len(self.active_upcoming_dates)
            + len(self.teacher_notes)
            + len(self.classroom_highlights)
        )

    def is_empty(self) -> bool:
        return self.daily_item_count == 0 and len(self.expired_action_items) == 0


def is_bloat(text: str) -> bool:
    """Check if a post is ClassDojo marketing or avatar up-sells."""
    lower = text.lower()
    for pattern in BLOAT_PATTERNS:
        if re.search(pattern, lower):
            return True
    return False


def _is_daily_relevant_temporal(
    eval_res: Dict[str, Any],
    is_fresh_post: bool,
    effective_digested: bool,
) -> bool:
    """Determine if a parsed action item or date belongs in today's daily-focused summary."""
    if eval_res.get("is_expired"):
        return False
    if eval_res.get("has_explicit_date"):
        if eval_res.get("is_today") or eval_res.get("is_tomorrow"):
            return True
        return is_fresh_post and not effective_digested
    return is_fresh_post and not effective_digested


class DigestEngine:
    """Extracts high-signal information and filters bloat from ClassDojo data."""

    def __init__(self, gemini_api_key: Optional[str] = None):
        self.gemini_api_key = gemini_api_key

    def synthesize(
        self,
        feed_items: List[Dict[str, Any]],
        messages: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        children: Optional[List[Dict[str, Any]]] = None,
        ref_dt: Optional[datetime] = None,
        daily_focus: bool = True,
        respect_digested: bool = True,
    ) -> Briefing:
        """
        Process feed posts, messages, and calendar events into a clean Briefing.
        When daily_focus=True (default), strictly surfaces items relevant to Today + Tomorrow
        plus new updates posted within the last 24 hours, omitting expired Past Notices.
        """
        if ref_dt is None:
            ref_dt = datetime.now().astimezone()
        ref_date = ref_dt.date()
        tomorrow_date = ref_date + timedelta(days=1)

        total_raw = len(feed_items) + len(messages) + len(events)
        bloat_count = 0

        action_items: List[ActionItem] = []
        upcoming_dates: List[UpcomingDate] = []
        teacher_notes: List[TeacherNote] = []
        highlights: List[ClassroomHighlight] = []

        child_names = [c.get("name") for c in (children or []) if c.get("name")]

        # 1. Process calendar events directly into Upcoming Dates
        for ev in events:
            title = ev.get("title", "School Event")
            desc = ev.get("description", "")
            start = ev.get("start_time") or "Upcoming"
            posted_time, _ = parse_and_format_timestamp(ev.get("fetched_at"), ref_dt=ref_dt)

            ev_date = parse_date_or_datetime(start, default_year=ref_date.year)
            is_stale_ev = bool(ev_date and ev_date < ref_date)

            if daily_focus:
                if is_stale_ev or ev_date is None or ev_date not in (ref_date, tomorrow_date):
                    continue
                if ev_date == ref_date and not title.lower().startswith("today"):
                    title = f"Today: {title}"
                elif ev_date == tomorrow_date and not title.lower().startswith("tomorrow"):
                    title = f"Tomorrow: {title}"

            upcoming_dates.append(UpcomingDate(
                title=title,
                date_str=start,
                details=desc or None,
                posted_at_str=posted_time,
                item_id=ev.get("id"),
                is_stale=is_stale_ev
            ))

        # 2. Process Direct Teacher Messages & Class Updates
        for msg in messages:
            body = (msg.get("body") or "").strip()
            if not body or is_bloat(body):
                bloat_count += 1
                continue

            sender = msg.get("sender_name") or "Teacher"
            msg_time_val = msg.get("message_timestamp")
            msg_dt = parse_raw_datetime(msg_time_val)
            posted_str, is_stale_msg = parse_and_format_timestamp(msg_time_val, ref_dt=ref_dt)
            is_already_digested_msg = bool(msg.get("digested_at"))
            effective_digested_msg = is_already_digested_msg if respect_digested else False
            is_fresh_msg = is_posted_within_24h(msg_dt, ref_dt)
            days_old_msg = (ref_date - msg_dt.date()).days if msg_dt else 0

            # Surface new teacher messages strictly from the last 24h when daily_focus=True
            if daily_focus:
                include_note = (not effective_digested_msg) and is_fresh_msg
            else:
                include_note = (not effective_digested_msg) and days_old_msg <= 7

            if include_note:
                teacher_notes.append(TeacherNote(
                    sender=sender,
                    body=body,
                    timestamp=msg_time_val,
                    posted_at_str=posted_str,
                    item_id=msg.get("id")
                ))

            # Scan multi-line message bodies line-by-line for dates and action items
            lines = [ln.strip() for ln in re.split(r"[\n]+", body) if ln.strip()]
            for ln in lines:
                lower_ln = ln.lower()
                if any(k in lower_ln for k in DATE_KEYWORDS):
                    date_eval = evaluate_item_temporal_status(
                        ln, msg_dt, ref_dt, context_text=body, daily_focus=daily_focus
                    )
                    if not daily_focus or _is_daily_relevant_temporal(date_eval, is_fresh_msg, effective_digested_msg):
                        date_title = (
                            date_eval["contextualized_summary"]
                            if (date_eval["is_today"] or date_eval.get("is_tomorrow"))
                            else ln
                        )
                        upcoming_dates.append(UpcomingDate(
                            title=date_title,
                            date_str=posted_str,
                            details=body[:250],
                            posted_at_str=posted_str,
                            author=sender,
                            item_id=msg.get("id"),
                            is_stale=date_eval["is_expired"]
                        ))
                if any(k in lower_ln for k in ACTION_KEYWORDS):
                    action_sent = self._extract_action_sentence(ln)
                    eval_res = evaluate_item_temporal_status(
                        action_sent, msg_dt, ref_dt, context_text=body, daily_focus=daily_focus
                    )
                    if not daily_focus or _is_daily_relevant_temporal(eval_res, is_fresh_msg, effective_digested_msg):
                        action_items.append(ActionItem(
                            summary=eval_res["contextualized_summary"] if not eval_res["is_expired"] else action_sent,
                            context=f"Message from {sender}",
                            author=sender,
                            urgency=eval_res["urgency"],
                            posted_at_str=posted_str,
                            is_stale=eval_res["is_expired"],
                            is_transient=eval_res.get("is_transient", False),
                            item_id=msg.get("id")
                        ))

        # 3. Process Story Feed items
        for item in feed_items:
            content = (item.get("content_text") or item.get("header") or "").strip()
            if not content:
                continue

            if is_bloat(content):
                bloat_count += 1
                continue

            item_id = item.get("id")
            # Resolve author name and class
            author = item.get("author_name") or item.get("senderName") or "Teacher / School"
            raw_time = item.get("item_timestamp")
            item_dt = parse_raw_datetime(raw_time)
            posted_str, is_stale_post = parse_and_format_timestamp(raw_time, ref_dt=ref_dt)
            lower = content.lower()

            # Check if this item has already been sent in a previous digest
            is_already_digested = bool(item.get("digested_at"))
            effective_digested = is_already_digested if respect_digested else False
            is_fresh_post = is_posted_within_24h(item_dt, ref_dt)

            # Attachments & Image URLs (only include images for new/undigested items)
            import json
            attachments = []
            try:
                raw_att = item.get("attachments") or item.get("attachments_json")
                if isinstance(raw_att, str):
                    attachments = json.loads(raw_att)
                elif isinstance(raw_att, list):
                    attachments = raw_att
            except Exception:
                attachments = []

            photo_count = 0
            video_count = 0
            file_count = 0
            image_urls = []
            video_urls = []
            file_urls = []

            for att in attachments:
                c = classify_attachment(att)
                if c["kind"] == "video":
                    video_count += 1
                    if c["media_url"]:
                        video_urls.append(c["media_url"])
                elif c["kind"] == "file":
                    file_count += 1
                    if c["media_url"]:
                        file_urls.append(c["media_url"])
                else:
                    photo_count += 1

                if not effective_digested and c["image_url"]:
                    image_urls.append(c["image_url"])

            # Check for dates / events in text (scan line-by-line so multi-bullet schedule posts capture all dates)
            post_lines = [ln.strip() for ln in re.split(r"[\n]+", content) if ln.strip()]
            found_date = False
            for ln in post_lines:
                if any(k in ln.lower() for k in DATE_KEYWORDS):
                    found_date = True
                    date_eval = evaluate_item_temporal_status(
                        ln, item_dt, ref_dt, context_text=content, daily_focus=daily_focus
                    )
                    if not daily_focus or _is_daily_relevant_temporal(date_eval, is_fresh_post, effective_digested):
                        date_title = (
                            date_eval["contextualized_summary"]
                            if (date_eval["is_today"] or date_eval.get("is_tomorrow"))
                            else ln
                        )
                        upcoming_dates.append(UpcomingDate(
                            title=date_title,
                            date_str=posted_str,
                            details=content[:200],
                            posted_at_str=posted_str,
                            author=author,
                            image_urls=image_urls,
                            video_urls=video_urls,
                            item_id=item_id,
                            is_stale=date_eval["is_expired"]
                        ))
            if not found_date and any(k in lower for k in DATE_KEYWORDS):
                sentence = self._extract_date_sentence(content)
                date_eval = evaluate_item_temporal_status(
                    sentence, item_dt, ref_dt, context_text=content, daily_focus=daily_focus
                )
                if not daily_focus or _is_daily_relevant_temporal(date_eval, is_fresh_post, effective_digested):
                    date_title = (
                        date_eval["contextualized_summary"]
                        if (date_eval["is_today"] or date_eval.get("is_tomorrow"))
                        else sentence
                    )
                    upcoming_dates.append(UpcomingDate(
                        title=date_title,
                        date_str=posted_str,
                        details=content[:200],
                        posted_at_str=posted_str,
                        author=author,
                        image_urls=image_urls,
                        video_urls=video_urls,
                        item_id=item_id,
                        is_stale=date_eval["is_expired"]
                    ))

            # Check for action items
            has_date_keyword = found_date or any(k in lower for k in DATE_KEYWORDS)
            if any(k in lower for k in ACTION_KEYWORDS):
                action_sent = self._extract_action_sentence(content)
                eval_res = evaluate_item_temporal_status(
                    action_sent, item_dt, ref_dt, context_text=content, daily_focus=daily_focus
                )
                if not daily_focus or _is_daily_relevant_temporal(eval_res, is_fresh_post, effective_digested):
                    action_items.append(ActionItem(
                        summary=eval_res["contextualized_summary"] if not eval_res["is_expired"] else action_sent,
                        context=f"Posted by {author}",
                        author=author,
                        urgency=eval_res["urgency"],
                        posted_at_str=posted_str,
                        is_stale=eval_res["is_expired"],
                        is_transient=eval_res.get("is_transient", False),
                        image_urls=image_urls,
                        video_urls=video_urls,
                        item_id=item_id
                    ))
            elif not has_date_keyword and not effective_digested and (not daily_focus or is_fresh_post):
                # Casual classroom highlight/photo update: include only if undigested and within last 24h
                highlights.append(ClassroomHighlight(
                    author=author,
                    text=content,
                    attachment_count=len(attachments),
                    photo_count=photo_count,
                    video_count=video_count,
                    file_count=file_count,
                    image_urls=image_urls,
                    video_urls=video_urls,
                    file_urls=file_urls,
                    date_str=raw_time,
                    posted_at_str=posted_str,
                    item_id=item_id
                ))

            # Process OCR extracted text, dates, and action items from image attachments
            ocr_data = item.get("ocr_json") or item.get("ocr_data")
            if ocr_data:
                if isinstance(ocr_data, str):
                    try:
                        ocr_data = json.loads(ocr_data)
                    except Exception:
                        ocr_data = None

                include_ocr_images = not effective_digested
                if isinstance(ocr_data, list):
                    for sub_ocr in ocr_data:
                        if isinstance(sub_ocr, dict):
                            self._integrate_ocr_into_briefing(
                                sub_ocr, author, posted_str, is_stale_post, action_items, upcoming_dates,
                                include_images=include_ocr_images, item_id=item_id, item_dt=item_dt, ref_dt=ref_dt,
                                daily_focus=daily_focus, effective_digested=effective_digested,
                            )
                elif isinstance(ocr_data, dict):
                    self._integrate_ocr_into_briefing(
                        ocr_data, author, posted_str, is_stale_post, action_items, upcoming_dates,
                        include_images=include_ocr_images, item_id=item_id, item_dt=item_dt, ref_dt=ref_dt,
                        daily_focus=daily_focus, effective_digested=effective_digested,
                    )

        # Deduplicate actions and dates by summary/title
        unique_actions = self._deduplicate_actions(action_items)
        unique_dates = self._deduplicate_dates(upcoming_dates)

        local_ref = ref_dt.astimezone()
        return Briefing(
            generated_at=local_ref.strftime("%A, %b %d, %Y"),
            period="Morning" if local_ref.hour < 12 else "Evening",
            children_names=child_names,
            action_items=unique_actions,
            upcoming_dates=unique_dates,
            teacher_notes=teacher_notes,
            classroom_highlights=highlights,
            raw_item_count=total_raw,
            filtered_bloat_count=bloat_count
        )

    def _extract_action_sentence(self, text: str) -> str:
        """Find the sentence containing the action keyword."""
        sentences = re.split(r"[.!?\n]+", text)
        for s in sentences:
            clean = s.strip()
            lower = clean.lower()
            if any(k in lower for k in ACTION_KEYWORDS):
                return clean
        return text.strip().split("\n")[0][:120]

    def _extract_date_sentence(self, text: str) -> str:
        sentences = re.split(r"[.!?\n]+", text)
        for s in sentences:
            clean = s.strip()
            lower = clean.lower()
            if any(k in lower for k in DATE_KEYWORDS):
                return clean
        return text.strip().split("\n")[0][:120]

    def _deduplicate_actions(self, items: List[ActionItem]) -> List[ActionItem]:
        seen = set()
        result = []
        for it in items:
            key = it.summary.lower().strip()
            if key not in seen and len(key) > 4:
                seen.add(key)
                result.append(it)
        return result

    def _deduplicate_dates(self, items: List[UpcomingDate]) -> List[UpcomingDate]:
        seen = set()
        result = []
        for it in items:
            key = it.title.lower().strip()
            if key not in seen and len(key) > 4:
                seen.add(key)
                result.append(it)
        return result

    def _integrate_ocr_into_briefing(
        self,
        ocr: Dict[str, Any],
        author: str,
        posted_str: str,
        is_stale_post: bool,
        action_items: List[ActionItem],
        upcoming_dates: List[UpcomingDate],
        include_images: bool = True,
        item_id: Optional[str] = None,
        item_dt: Optional[datetime] = None,
        ref_dt: Optional[datetime] = None,
        daily_focus: bool = True,
        effective_digested: bool = False,
    ) -> None:
        if not ocr.get("has_text"):
            return

        img_urls = [ocr["image_url"]] if (include_images and ocr.get("image_url")) else []
        effective_ref = ref_dt if ref_dt is not None else datetime.now().astimezone()
        ref_date = effective_ref.date()
        tomorrow_date = ref_date + timedelta(days=1)
        is_fresh_post = is_posted_within_24h(item_dt, effective_ref)

        # Dates from image
        for d in ocr.get("dates", []):
            if isinstance(d, dict):
                title = d.get("title") or "School Event"
                date_info = d.get("date_str") or "Upcoming"
                if d.get("time"):
                    date_info += f" at {d['time']}"
                loc = f" ({d['location']})" if d.get("location") else ""
                full_title = f"{title}{loc}"
            else:
                full_title = str(d)
                date_info = str(d)

            d_date = parse_date_or_datetime(date_info, default_year=ref_date.year)
            if d_date is not None:
                is_stale_d = d_date < ref_date
                is_today_d = d_date == ref_date
                is_tomorrow_d = d_date == tomorrow_date
                if daily_focus:
                    if is_stale_d:
                        continue
                    if not (is_today_d or is_tomorrow_d or (is_fresh_post and not effective_digested)):
                        continue
            else:
                d_eval = evaluate_item_temporal_status(
                    f"{full_title} {date_info}", item_dt, effective_ref, daily_focus=daily_focus
                )
                is_stale_d = d_eval["is_expired"]
                is_today_d = d_eval["is_today"]
                is_tomorrow_d = d_eval.get("is_tomorrow", False)
                if daily_focus and not _is_daily_relevant_temporal(d_eval, is_fresh_post, effective_digested):
                    continue

            if daily_focus:
                if is_today_d and not full_title.lower().startswith("today"):
                    full_title = f"Today: {full_title}"
                elif is_tomorrow_d and not full_title.lower().startswith("tomorrow"):
                    full_title = f"Tomorrow: {full_title}"

            upcoming_dates.append(UpcomingDate(
                title=full_title,
                date_str=date_info,
                details=f"From flyer/image posted by {author}",
                posted_at_str=posted_str,
                author=author,
                image_urls=img_urls,
                item_id=item_id,
                is_stale=is_stale_d
            ))

        # Action items from image
        for act in ocr.get("action_items", []):
            summary = act.get("summary") if isinstance(act, dict) else str(act)
            eval_res = evaluate_item_temporal_status(summary, item_dt, effective_ref, daily_focus=daily_focus)
            if daily_focus and not _is_daily_relevant_temporal(eval_res, is_fresh_post, effective_digested):
                continue
            action_items.append(ActionItem(
                summary=f"[Flyer Note] {eval_res['contextualized_summary'] if not eval_res['is_expired'] else summary}",
                context=f"From photo/flyer posted by {author}",
                author=author,
                urgency=eval_res["urgency"],
                posted_at_str=posted_str,
                is_stale=eval_res["is_expired"],
                is_transient=eval_res.get("is_transient", False),
                image_urls=img_urls,
                item_id=item_id
            ))

    def format_plain_text(self, briefing: Briefing) -> str:
        """Render the briefing as formatted plain text for terminal or text-only emails."""
        lines = []
        title_date = briefing.generated_at
        lines.append("=" * 60)
        lines.append(f"🎒 DOJOZEN DAILY BRIEFING — {title_date}")
        if briefing.children_names:
            lines.append(f"Students: {', '.join(briefing.children_names)}")
        lines.append("=" * 60)
        lines.append("")

        if briefing.is_empty():
            lines.append("✅ All caught up for today! Nothing due today/tomorrow and no new updates in the last 24h.")
            lines.append(f"(Processed {briefing.raw_item_count} items, filtered {briefing.filtered_bloat_count} promotional items)")
            return "\n".join(lines)

        active = briefing.active_action_items

        if active:
            lines.append("🚨 TODAY'S ACTION ITEMS & TO-DOS:")
            for item in active:
                badge = "[URGENT] " if item.urgency == "high" else "• "
                lines.append(f"  {badge}{item.summary}")
                author_str = f" · {item.author}" if item.author else ""
                lines.append(f"     └─ Posted: {item.posted_at_str}{author_str}")
            lines.append("")

        active_dates = briefing.active_upcoming_dates
        if active_dates:
            lines.append("📅 TODAY & TOMORROW'S SCHEDULE:")
            for d in active_dates:
                author_str = f" · {d.author}" if d.author else ""
                lines.append(f"  • {d.title}")
                lines.append(f"     └─ Posted: {d.posted_at_str}{author_str}")
                if d.details and d.details != d.title:
                    lines.append(f"        \"{d.details[:100]}\"")
            lines.append("")

        if briefing.teacher_notes:
            lines.append("💬 NEW TEACHER MESSAGES (LAST 24H):")
            for msg in briefing.teacher_notes:
                lines.append(f"  • From {msg.sender} ({msg.posted_at_str}):")
                lines.append(f"     \"{msg.body}\"")
            lines.append("")

        if briefing.classroom_highlights:
            lines.append("🌟 TODAY'S CLASSROOM HIGHLIGHTS:")
            for h in briefing.classroom_highlights[:5]:
                label = h.attachment_label
                photo_str = f" [{label}]" if label else ""
                lines.append(f"  • {h.author} ({h.posted_at_str}): {h.text[:120]}{photo_str}")
            lines.append("")

        lines.append("-" * 60)
        lines.append(
            f"Filtered out {briefing.filtered_bloat_count} marketing/bloat items. Total processed: {briefing.raw_item_count}"
        )
        return "\n".join(lines)

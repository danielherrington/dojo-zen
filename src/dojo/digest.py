"""Anti-bloat digest and summarization engine for ClassDojo content."""

import re
from datetime import datetime, timezone, date, timedelta
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

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
    "project due", "library books", "snack", "jacket"
]

DATE_KEYWORDS = [
    "early dismissal", "early release", "no school", "holiday", "closed",
    "field trip", "picture day", "spirit week", "conference", "assembly",
    "book fair", "parade", "open house", "half day", "starts at", "ends at", "weekend"
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


def parse_date_or_datetime(val: Any) -> Optional[date]:
    """Parse string, datetime, or date into a date object."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
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
        y_num = int(date_match.group(3)) if date_match.group(3) else datetime.now().year
        try:
            return date(y_num, m_num, d_num)
        except Exception:
            pass
    return None


def evaluate_item_temporal_status(
    text: str,
    post_dt: Optional[datetime],
    ref_dt: Optional[datetime] = None
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
    days_since_post = (ref_date - post_date).days
    lower = text.lower()

    # 1. "Tomorrow" / "tmrw"
    if re.search(r"\b(tomorrow|tmrw)\b", lower):
        target_date = post_date + timedelta(days=1)
        if target_date < ref_date:
            return {
                "is_expired": True,
                "is_today": False,
                "is_transient": True,
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
                "is_transient": True,
                "target_date": target_date,
                "contextualized_summary": rewritten,
                "urgency": "high",
            }
        else:
            rewritten = text if text.lower().startswith("tomorrow") else f"Tomorrow: {text}"
            return {
                "is_expired": False,
                "is_today": False,
                "is_transient": True,
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
                "is_transient": True,
                "target_date": target_date,
                "contextualized_summary": text,
                "urgency": "expired",
            }
        elif target_date == ref_date:
            return {
                "is_expired": False,
                "is_today": True,
                "is_transient": True,
                "target_date": target_date,
                "contextualized_summary": text,
                "urgency": "high",
            }

    # 3. Explicit Calendar Dates (e.g. "Sep 18", "September 21st", "9/21")
    date_match = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?\b",
        lower,
    )
    if date_match:
        month_name = date_match.group(1)[:3].lower()
        month_num = MONTHS.get(month_name, 1)
        day_num = int(date_match.group(2))
        try:
            target_date = date(post_date.year, month_num, day_num)
            if target_date < ref_date:
                return {
                    "is_expired": True,
                    "is_today": False,
                    "is_transient": True,
                    "target_date": target_date,
                    "contextualized_summary": text,
                    "urgency": "expired",
                }
            elif target_date == ref_date:
                rewritten = text if text.lower().startswith("today") else f"Today: {text}"
                return {
                    "is_expired": False,
                    "is_today": True,
                    "is_transient": True,
                    "target_date": target_date,
                    "contextualized_summary": rewritten,
                    "urgency": "high",
                }
            else:
                return {
                    "is_expired": False,
                    "is_today": False,
                    "is_transient": True,
                    "target_date": target_date,
                    "contextualized_summary": text,
                    "urgency": "normal",
                }
        except ValueError:
            pass

    # 4. "This weekend", "the weekend"
    if "this weekend" in lower or "the weekend" in lower:
        days_to_sun = (6 - post_date.weekday()) % 7
        target_sunday = post_date + timedelta(days=days_to_sun)
        if ref_date > target_sunday:
            return {
                "is_expired": True,
                "is_today": False,
                "is_transient": True,
                "target_date": target_sunday,
                "contextualized_summary": text,
                "urgency": "expired",
            }
        elif ref_date >= post_date + timedelta(days=(5 - post_date.weekday()) % 7):
            return {
                "is_expired": False,
                "is_today": True,
                "is_transient": True,
                "target_date": target_sunday,
                "contextualized_summary": text,
                "urgency": "high",
            }
        else:
            return {
                "is_expired": False,
                "is_today": False,
                "is_transient": True,
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

        day_diff = (target_weekday - post_weekday) % 7
        if "next" in lower and day_diff == 0:
            day_diff = 7

        target_date = post_date + timedelta(days=day_diff)
        if target_date < ref_date:
            return {
                "is_expired": True,
                "is_today": False,
                "is_transient": True,
                "target_date": target_date,
                "contextualized_summary": text,
                "urgency": "expired",
            }
        elif target_date == ref_date:
            rewritten = text if text.lower().startswith("today") else f"Today ({target_day_name.capitalize()}): {text}"
            return {
                "is_expired": False,
                "is_today": True,
                "is_transient": True,
                "target_date": target_date,
                "contextualized_summary": rewritten,
                "urgency": "high",
            }
        else:
            return {
                "is_expired": False,
                "is_today": False,
                "is_transient": True,
                "target_date": target_date,
                "contextualized_summary": text,
                "urgency": "normal",
            }

    # 6. Fallback for undated posts:
    if days_since_post >= 3:
        return {
            "is_expired": True,
            "is_today": False,
            "is_transient": False,
            "target_date": post_date,
            "contextualized_summary": text,
            "urgency": "expired",
        }

    return {
        "is_expired": False,
        "is_today": days_since_post == 0,
        "is_transient": False,
        "target_date": post_date,
        "contextualized_summary": text,
        "urgency": "normal",
    }


def parse_and_format_timestamp(raw_time: Optional[str]) -> Tuple[str, bool]:
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
        now = datetime.now().astimezone()
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


class ActionItem(BaseModel):
    summary: str
    context: str
    author: Optional[str] = None
    urgency: str = "normal"  # "high", "normal", "expired"
    posted_at_str: str = "Recently"
    is_stale: bool = False
    is_transient: bool = False
    image_urls: List[str] = Field(default_factory=list)
    item_id: Optional[str] = None


class UpcomingDate(BaseModel):
    title: str
    date_str: str
    details: Optional[str] = None
    posted_at_str: Optional[str] = None
    author: Optional[str] = None
    image_urls: List[str] = Field(default_factory=list)
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
    image_urls: List[str] = Field(default_factory=list)
    date_str: Optional[str] = None
    posted_at_str: str = "Recently"
    item_id: Optional[str] = None


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

    def is_empty(self) -> bool:
        return (
            len(self.action_items) == 0
            and len(self.upcoming_dates) == 0
            and len(self.teacher_notes) == 0
            and len(self.classroom_highlights) == 0
        )


def is_bloat(text: str) -> bool:
    """Check if a post is ClassDojo marketing or avatar up-sells."""
    lower = text.lower()
    for pattern in BLOAT_PATTERNS:
        if re.search(pattern, lower):
            return True
    return False


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
        ref_dt: Optional[datetime] = None
    ) -> Briefing:
        """
        Process feed posts, messages, and calendar events into a clean Briefing.
        Evaluates relative dates (tomorrow -> Today, past deadlines filtered out).
        """
        if ref_dt is None:
            ref_dt = datetime.now().astimezone()
        ref_date = ref_dt.date()

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
            posted_time, _ = parse_and_format_timestamp(ev.get("fetched_at"))

            ev_date = parse_date_or_datetime(start)
            is_stale_ev = bool(ev_date and ev_date < ref_date)

            upcoming_dates.append(UpcomingDate(
                title=title,
                date_str=start,
                details=desc or None,
                posted_at_str=posted_time,
                item_id=ev.get("id"),
                is_stale=is_stale_ev
            ))

        # 2. Process Direct Teacher Messages
        for msg in messages:
            body = (msg.get("body") or "").strip()
            if not body or is_bloat(body):
                bloat_count += 1
                continue

            sender = msg.get("sender_name") or "Teacher"
            msg_time_val = msg.get("message_timestamp")
            msg_dt = parse_raw_datetime(msg_time_val)
            posted_str, is_stale_msg = parse_and_format_timestamp(msg_time_val)

            teacher_notes.append(TeacherNote(
                sender=sender,
                body=body,
                timestamp=msg_time_val,
                posted_at_str=posted_str,
                item_id=msg.get("id")
            ))

            # Scan message body for action items
            lower_body = body.lower()
            if any(k in lower_body for k in ACTION_KEYWORDS):
                action_sent = self._extract_action_sentence(body)
                eval_res = evaluate_item_temporal_status(action_sent, msg_dt, ref_dt)
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
            posted_str, is_stale_post = parse_and_format_timestamp(raw_time)
            lower = content.lower()

            # Check if this item has already been sent in a previous digest
            is_already_digested = bool(item.get("digested_at"))

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

            image_urls = []
            if not is_already_digested:
                image_urls = [
                    att.get("path") or att.get("url")
                    for att in attachments
                    if isinstance(att, dict) and (att.get("path") or att.get("url"))
                ]

            # Check for dates / events in text
            if any(k in lower for k in DATE_KEYWORDS):
                sentence = self._extract_date_sentence(content)
                date_eval = evaluate_item_temporal_status(sentence, item_dt, ref_dt)
                date_title = date_eval["contextualized_summary"] if date_eval["is_today"] else sentence
                upcoming_dates.append(UpcomingDate(
                    title=date_title,
                    date_str=posted_str,
                    details=content[:200],
                    posted_at_str=posted_str,
                    author=author,
                    image_urls=image_urls,
                    item_id=item_id,
                    is_stale=date_eval["is_expired"]
                ))

            # Check for action items
            if any(k in lower for k in ACTION_KEYWORDS):
                action_sent = self._extract_action_sentence(content)
                eval_res = evaluate_item_temporal_status(action_sent, item_dt, ref_dt)
                action_items.append(ActionItem(
                    summary=eval_res["contextualized_summary"] if not eval_res["is_expired"] else action_sent,
                    context=f"Posted by {author}",
                    author=author,
                    urgency=eval_res["urgency"],
                    posted_at_str=posted_str,
                    is_stale=eval_res["is_expired"],
                    is_transient=eval_res.get("is_transient", False),
                    image_urls=image_urls,
                    item_id=item_id
                ))
            elif not is_already_digested:
                # If not an action item or date, it's a classroom highlight/update (skip if already digested)
                highlights.append(ClassroomHighlight(
                    author=author,
                    text=content,
                    attachment_count=len(attachments),
                    image_urls=image_urls,
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

                include_ocr_images = not is_already_digested
                if isinstance(ocr_data, list):
                    for sub_ocr in ocr_data:
                        if isinstance(sub_ocr, dict):
                            self._integrate_ocr_into_briefing(
                                sub_ocr, author, posted_str, is_stale_post, action_items, upcoming_dates,
                                include_images=include_ocr_images, item_id=item_id, item_dt=item_dt, ref_dt=ref_dt
                            )
                elif isinstance(ocr_data, dict):
                    self._integrate_ocr_into_briefing(
                        ocr_data, author, posted_str, is_stale_post, action_items, upcoming_dates,
                        include_images=include_ocr_images, item_id=item_id, item_dt=item_dt, ref_dt=ref_dt
                    )

        # Deduplicate actions and dates by summary/title
        unique_actions = self._deduplicate_actions(action_items)
        unique_dates = self._deduplicate_dates(upcoming_dates)

        return Briefing(
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
        ref_dt: Optional[datetime] = None
    ) -> None:
        if not ocr.get("has_text"):
            return

        img_urls = [ocr["image_url"]] if (include_images and ocr.get("image_url")) else []
        ref_date = ref_dt.date() if ref_dt else datetime.now().astimezone().date()

        # Dates from image
        for d in ocr.get("dates", []):
            title = d.get("title") or "School Event"
            date_info = d.get("date_str") or "Upcoming"
            d_date = parse_date_or_datetime(date_info)
            is_stale_d = bool(d_date and d_date < ref_date)
            if d.get("time"):
                date_info += f" at {d['time']}"
            loc = f" ({d['location']})" if d.get("location") else ""
            upcoming_dates.append(UpcomingDate(
                title=f"{title}{loc}",
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
            eval_res = evaluate_item_temporal_status(summary, item_dt, ref_dt)
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
            lines.append("✅ All caught up! No new action items, messages, or announcements.")
            lines.append(f"(Processed {briefing.raw_item_count} items, filtered {briefing.filtered_bloat_count} promotional items)")
            return "\n".join(lines)

        active = briefing.active_action_items
        expired = briefing.expired_action_items

        if active:
            lines.append("🚨 CURRENT ACTION ITEMS & TO-DOS:")
            for item in active:
                badge = "[URGENT] " if item.urgency == "high" else "• "
                lines.append(f"  {badge}{item.summary}")
                author_str = f" · {item.author}" if item.author else ""
                lines.append(f"     └─ Posted: {item.posted_at_str}{author_str}")
            lines.append("")

        if expired:
            lines.append("📜 PAST NOTICES (From previous days):")
            for item in expired[:5]:
                author_str = f" · {item.author}" if item.author else ""
                lines.append(f"  • [EXPIRED] {item.summary}")
                lines.append(f"     └─ Posted: {item.posted_at_str}{author_str}")
            lines.append("")

        active_dates = briefing.active_upcoming_dates
        if active_dates:
            lines.append("📅 UPCOMING DATES & SCHEDULE:")
            for d in active_dates:
                author_str = f" · {d.author}" if d.author else ""
                lines.append(f"  • {d.title}")
                lines.append(f"     └─ Posted: {d.posted_at_str}{author_str}")
                if d.details and d.details != d.title:
                    lines.append(f"        \"{d.details[:100]}\"")
            lines.append("")

        if briefing.teacher_notes:
            lines.append("💬 TEACHER MESSAGES:")
            for msg in briefing.teacher_notes:
                lines.append(f"  • From {msg.sender} ({msg.posted_at_str}):")
                lines.append(f"     \"{msg.body}\"")
            lines.append("")

        if briefing.classroom_highlights:
            lines.append("🌟 CLASSROOM HIGHLIGHTS:")
            for h in briefing.classroom_highlights[:5]:
                photo_str = f" [{h.attachment_count} photos]" if h.attachment_count > 0 else ""
                lines.append(f"  • {h.author} ({h.posted_at_str}): {h.text[:120]}{photo_str}")
            lines.append("")

        lines.append("-" * 60)
        lines.append(
            f"Filtered out {briefing.filtered_bloat_count} marketing/bloat items. Total processed: {briefing.raw_item_count}"
        )
        return "\n".join(lines)

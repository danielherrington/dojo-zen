"""Intelligent urgency evaluation engine for incoming ClassDojo messages and announcements."""

import re
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from dojo.digest import is_bloat


class UrgencyLevel(str, Enum):
    IMMEDIATE = "immediate"  # Alert immediately via push / urgent email
    ROUTINE = "routine"      # Hold for daily recap briefing
    BLOAT = "bloat"          # Discard (marketing / monster dressing / ad)


class AlertDecision(BaseModel):
    item_id: str
    item_type: str  # 'message' or 'feed'
    sender_or_author: str
    title: str
    body: str
    urgency: UrgencyLevel
    reason: str
    confidence: float = 1.0
    action_required: Optional[str] = None
    timestamp: Optional[str] = None


# Patterns requiring immediate notification
HEALTH_EMERGENCY_PATTERNS = [
    r"\bnurse\b",
    r"\bfever\b",
    r"\bvomi[tt]",
    r"\bsick\b",
    r"\binjur(ed|y)\b",
    r"\bhurt\b",
    r"\bbleeding\b",
    r"\bconcussion\b",
    r"\bhead bump\b",
    r"\bice pack\b",
    r"\binhaler\b",
    r"\ballerg(y|ic)\b",
    r"\bepi-?pen\b",
    r"\bclinic\b",
    r"\bincident\b",
]

SAME_DAY_LOGISTICS_PATTERNS = [
    r"early dismissal (today|now)",
    r"dismissing early (today|now)",
    r"school closing (today|early)",
    r"pickup (today|change|immediately)",
    r"bus (\#?\d+|\w+) (is )?delayed",
    r"bus breakdown|broken down",
    r"after-?school (is )?cancell?ed",
    r"after-?care (is )?cancell?ed",
    r"weather closure",
    r"lockdown|evacuation|shelter in place",
    r"power outage",
    r"water main break",
]

IMMEDIATE_TEACHER_REQUEST_PATTERNS = [
    r"please call me\b",
    r"please call (the )?(office|school)\b",
    r"call (me )?(asap|as soon as possible)\b",
    r"need to speak with you (today|immediately|asap)\b",
    r"\burgent\b",
    r"\basap\b",
    r"emergency contact",
    r"please contact us immediately",
]


class AlertEvaluator:
    """Evaluates incoming messages to decide whether to trigger an immediate alert or hold for daily recap."""

    def __init__(self, gemini_api_key: Optional[str] = None):
        self.gemini_api_key = gemini_api_key

    def evaluate_message(self, message: Dict[str, Any]) -> AlertDecision:
        """Evaluate a direct teacher message for urgency."""
        msg_id = str(message.get("id") or message.get("_id"))
        sender = message.get("sender_name") or "Teacher"
        body = (message.get("body") or "").strip()
        time_val = message.get("message_timestamp")

        if is_bloat(body) or not body:
            return AlertDecision(
                item_id=msg_id,
                item_type="message",
                sender_or_author=sender,
                title=f"Message from {sender}",
                body=body,
                urgency=UrgencyLevel.BLOAT,
                reason="Marketing or promotional noise",
                timestamp=time_val
            )

        lower = body.lower()

        # Check 1: Health & Medical Safety
        for pattern in HEALTH_EMERGENCY_PATTERNS:
            if re.search(pattern, lower):
                return AlertDecision(
                    item_id=msg_id,
                    item_type="message",
                    sender_or_author=sender,
                    title=f"Health / Safety Alert: Message from {sender}",
                    body=body,
                    urgency=UrgencyLevel.IMMEDIATE,
                    reason=f"Health/Safety keyword detected ('{pattern}')",
                    action_required="Check on your child or contact the school/nurse",
                    timestamp=time_val
                )

        # Check 2: Same-day logistics & pickup
        for pattern in SAME_DAY_LOGISTICS_PATTERNS:
            if re.search(pattern, lower):
                return AlertDecision(
                    item_id=msg_id,
                    item_type="message",
                    sender_or_author=sender,
                    title=f"Schedule / Pickup Alert: Message from {sender}",
                    body=body,
                    urgency=UrgencyLevel.IMMEDIATE,
                    reason=f"Same-day logistics notice ('{pattern}')",
                    action_required="Review pickup or transport schedule",
                    timestamp=time_val
                )

        # Check 3: Direct phone/contact request from teacher
        for pattern in IMMEDIATE_TEACHER_REQUEST_PATTERNS:
            if re.search(pattern, lower):
                return AlertDecision(
                    item_id=msg_id,
                    item_type="message",
                    sender_or_author=sender,
                    title=f"Immediate Response Requested: Message from {sender}",
                    body=body,
                    urgency=UrgencyLevel.IMMEDIATE,
                    reason="Teacher requested immediate communication",
                    action_required="Call or reply to the teacher as soon as possible",
                    timestamp=time_val
                )

        # Non-urgent direct message -> Routine (held for daily recap)
        return AlertDecision(
            item_id=msg_id,
            item_type="message",
            sender_or_author=sender,
            title=f"Message from {sender}",
            body=body,
            urgency=UrgencyLevel.ROUTINE,
            reason="Routine communication; queued for daily recap briefing",
            timestamp=time_val
        )

    def evaluate_feed_item(self, item: Dict[str, Any]) -> AlertDecision:
        """Evaluate a classroom or school story post for urgency."""
        item_id = str(item.get("id") or item.get("_id"))
        author = item.get("author_name") or "School"
        header = item.get("header") or ""
        body = (item.get("content_text") or header).strip()
        time_val = item.get("item_timestamp")

        if is_bloat(body) or not body:
            return AlertDecision(
                item_id=item_id,
                item_type="feed",
                sender_or_author=author,
                title=header or f"Post from {author}",
                body=body,
                urgency=UrgencyLevel.BLOAT,
                reason="Marketing or promotional noise",
                timestamp=time_val
            )

        lower = (header + " " + body).lower()

        # School-wide emergency / early closure / transport emergency
        for pattern in SAME_DAY_LOGISTICS_PATTERNS:
            if re.search(pattern, lower):
                return AlertDecision(
                    item_id=item_id,
                    item_type="feed",
                    sender_or_author=author,
                    title=f"Urgent School Notice: {header or author}",
                    body=body,
                    urgency=UrgencyLevel.IMMEDIATE,
                    reason=f"Urgent same-day schedule change ('{pattern}')",
                    action_required="Check school closure or pickup plan",
                    timestamp=time_val
                )

        for pattern in HEALTH_EMERGENCY_PATTERNS:
            if re.search(pattern, lower) and ("today" in lower or "immediate" in lower or "outbreak" in lower):
                return AlertDecision(
                    item_id=item_id,
                    item_type="feed",
                    sender_or_author=author,
                    title=f"Health Notice: {header or author}",
                    body=body,
                    urgency=UrgencyLevel.IMMEDIATE,
                    reason="Urgent health notice posted",
                    action_required="Review health advisory",
                    timestamp=time_val
                )

        return AlertDecision(
            item_id=item_id,
            item_type="feed",
            sender_or_author=author,
            title=header or f"Announcement from {author}",
            body=body,
            urgency=UrgencyLevel.ROUTINE,
            reason="Standard story post; queued for daily recap briefing",
            timestamp=time_val
        )

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
    # Set by MessageMonitor for IMMEDIATE alerts: "sent", "failed: <error>" or "dry_run".
    delivery_status: Optional[str] = None
    is_retry: bool = False


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
    r"\b(hit|bumped|banged) (his|her|their) head\b",
    r"\bice pack\b",
    r"\binhaler\b",
    r"\ballerg(y|ic)\b",
    r"\bepi-?pen\b",
    r"\bclinic\b",
    r"\bincident\b",
    r"\bambulance\b",
    r"\bstitches\b",
    r"\b911\b",
    r"\bthrew up\b",
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


# --- False-alarm guards for direct / Class Updates messages (DAN-164) ---
# Bias toward recall: a guard only downgrades a keyword hit when the sentence carries no
# strong, child-specific emergency signal. Missing a real emergency is worse than a false alarm.

NEGATORS = {
    "no", "not", "nothing", "never", "none", "nobody", "without",
    "isn't", "wasn't", "weren't", "aren't", "don't", "doesn't", "didn't", "won't",
    "isnt", "wasnt", "werent", "arent", "dont", "doesnt", "didnt", "wont",
}
NEGATION_WINDOW = 3  # tokens before the keyword that can negate it ("no one was hurt")

# Sentences that are general instructions, policies or scheduled info rather than an event.
POLICY_REMINDER_PATTERNS = [
    # Generic subjects only: "if she gets worse" about a specific child must still alert.
    r"\bif (your (child|student|kid|little one)|a (child|student)|students|anyone|children|kids)\b",
    r"\bkeep (your (child|student|kid|little one)|children|students|kids|sick (children|kids|students))\b.{0,20}\bhome\b",
    r"\b(allergy|allergen|nut|peanut)[- ](friendly|free|safe|aware)\b",
    r"\bfever[- ]free\b",
    r"\b(as a )?reminder\b|\bplease remember\b|\bdon't forget\b|\bpolicy\b",
    r"\bwash(ing)? (their |your )?hands\b|\bgerms?\b|\b(cold|flu) season\b",
    r"\b(list|note|share|let us know|tell us|inform us|update us)\b.{0,40}\ballerg",
    r"\bany (food )?allerg",
    r"\b(screening|screenings|flu shots?|vaccin\w*|immuniz\w*)\b",
    r"\bout sick\b|\bsick (day|days|note|leave)\b",
    r"\bfeelings?\b.{0,25}\bhurt\b|\bhurt\b.{0,10}\bfeelings?\b",
    r"\bnurse('s office)? will be (closed|out|away|unavailable)\b",
    r"\bfire drill\b|\bsafety drill\b|\blockdown drill\b",
]

# Bare "urgent"/"asap" attached to paperwork or errands is a deadline, not an emergency.
PAPERWORK_PATTERNS = [
    r"\b(forms?|slips?|permission|money|payment|orders?|fees?|book ?fair|picture day|library books?|rsvp|sign-?up)\b",
]
BARE_URGENCY_PATTERNS = {r"\burgent\b", r"\basap\b"}

# Child-specific, act-now signals that override the policy/paperwork guards.
STRONG_EMERGENCY_PATTERNS = [
    r"\b(pick (him|her|them|\w+) up|come (pick|get)|please come)\b",
    r"\b(went|came|was sent|been sent|is at|is with) (to )?the (nurse|health office|clinic)\b",
    r"\bin the nurse'?s office with\b",
    r"\bcall (me|the office|the school|us)\b",
    r"\b(right now|immediately|ambulance|911)\b",
]

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_CLAUSE_SPLIT = re.compile(r"[,;:\u2014\u2013]|\s-\s|\bbut\b|\bhowever\b")
_TOKEN = re.compile(r"[a-z0-9']+")


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _is_negated(clause: str, match_start: int) -> bool:
    before = _TOKEN.findall(clause[:match_start].replace("\u2019", "'"))
    return any(tok in NEGATORS for tok in before[-NEGATION_WINDOW:])


def _any(patterns: List[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def find_guarded_match(patterns: List[str], text: str) -> Optional[str]:
    """Return the first pattern that matches ``text`` and survives the false-alarm guards.

    Each sentence is checked on its own; inside a sentence, negation is scoped to the clause
    (so "no fever, but she vomited" still alerts on "vomited").
    """
    for sentence in _sentences(text):
        strong = _any(STRONG_EMERGENCY_PATTERNS, sentence)
        policy = _any(POLICY_REMINDER_PATTERNS, sentence)
        paperwork = _any(PAPERWORK_PATTERNS, sentence)
        for clause in _CLAUSE_SPLIT.split(sentence):
            if not clause or not clause.strip():
                continue
            for pattern in patterns:
                m = re.search(pattern, clause)
                if not m:
                    continue
                if _is_negated(clause, m.start()):
                    continue
                if policy and not strong:
                    continue
                if pattern in BARE_URGENCY_PATTERNS and paperwork and not strong:
                    continue
                return pattern
    return None


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
        pattern = find_guarded_match(HEALTH_EMERGENCY_PATTERNS, lower)
        if pattern:
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
        pattern = find_guarded_match(SAME_DAY_LOGISTICS_PATTERNS, lower)
        if pattern:
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
        pattern = find_guarded_match(IMMEDIATE_TEACHER_REQUEST_PATTERNS, lower)
        if pattern:
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

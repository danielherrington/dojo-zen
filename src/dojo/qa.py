"""Grounded Question Answering and Search Engine for ClassDojo data."""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from dojo.db import DojoDatabase
from dojo.digest import parse_and_format_timestamp, is_bloat


class SourceCitation(BaseModel):
    author: str
    posted_at_str: str
    snippet: str
    item_type: str  # 'feed', 'message', 'event'
    item_id: str


class QAResult(BaseModel):
    query: str
    answer: str
    sources: List[SourceCitation] = Field(default_factory=list)
    confidence: str = "high"  # "high", "medium", "low"
    suggested_followups: List[str] = Field(default_factory=list)


INTENT_KEYWORDS = {
    "homework": ["homework", "packet", "assignment", "worksheet", "reading 15", "practice", "math homework", "fluency"],
    "supplies": ["bring", "wear", "shoes", "sneakers", "headphones", "bookbag", "box", "supplies", "folder", "snack", "water bottle", "shirt"],
    "testing": ["test", "assessment", "fast", "smart start", "quiz", "exam", "score"],
    "dates": ["date", "when", "holiday", "closed", "no school", "early release", "early dismissal", "break", "labor day", "friday", "calendar"],
    "celebration": ["celebration", "5 senses", "party", "birthday", "detective", "spirit", "costume", "pumpkin"],
    "reading": ["sight words", "reading", "words", "ela", "book", "library"],
}


class DojoQA:
    """Answers parent questions about classroom events using the local SQLite store."""

    def __init__(self, db: DojoDatabase, gemini_api_key: Optional[str] = None):
        self.db = db
        self.gemini_api_key = gemini_api_key

    def answer_question(self, query: str) -> QAResult:
        """Search stored feed posts, messages, and events, and answer the query."""
        clean_q = query.strip().lower()
        if not clean_q:
            return QAResult(
                query=query,
                answer="Please ask a question about your child's class (e.g., 'Is there homework tonight?' or 'What should he bring?')."
            )

        # Retrieve all non-bloat records from SQLite
        all_records = self._load_searchable_records()

        # Score and rank records
        scored_records = self._rank_records(clean_q, all_records)

        if not scored_records:
            return QAResult(
                query=query,
                answer=(
                    "I searched through your classroom posts, messages, and calendar events, but didn't find "
                    "any mentions related to your question. If this was discussed recently, try clicking the "
                    "'Sync Now' button to check for new ClassDojo updates."
                ),
                confidence="low"
            )

        # Synthesize answer from top matches
        top_matches = scored_records[:5]
        sources: List[SourceCitation] = []
        for rec in top_matches:
            author = rec.get("author") or rec.get("sender_name") or "Teacher"
            posted_str, _ = parse_and_format_timestamp(rec.get("timestamp"))
            snippet = (rec.get("text") or rec.get("title") or "")[:250].strip()
            sources.append(SourceCitation(
                author=author,
                posted_at_str=posted_str,
                snippet=snippet,
                item_type=rec.get("type", "feed"),
                item_id=str(rec.get("id"))
            ))

        answer_text = self._synthesize_answer(clean_q, top_matches)

        # Determine suggested follow-ups based on query
        followups = self._generate_followups(clean_q)

        return QAResult(
            query=query,
            answer=answer_text,
            sources=sources,
            confidence="high" if len(top_matches) > 0 else "medium",
            suggested_followups=followups
        )

    def _load_searchable_records(self) -> List[Dict[str, Any]]:
        """Fetch all feed posts, messages, and events from database."""
        records = []
        # Feeds
        import json
        for r in self.db.get_all_feed_items():
            text = (r.get("content_text") or r.get("header") or "").strip()

            # Include OCR text from image attachments
            ocr_val = r.get("ocr_json") or r.get("ocr_data")
            if ocr_val:
                try:
                    parsed_ocr = json.loads(ocr_val) if isinstance(ocr_val, str) else ocr_val
                    ocr_parts = []
                    if isinstance(parsed_ocr, list):
                        for sub in parsed_ocr:
                            if isinstance(sub, dict) and sub.get("full_text"):
                                ocr_parts.append(sub["full_text"])
                    elif isinstance(parsed_ocr, dict) and parsed_ocr.get("full_text"):
                        ocr_parts.append(parsed_ocr["full_text"])
                    if ocr_parts:
                        text = f"{text}\n[Flyer / Image Transcript: {' | '.join(ocr_parts)}]".strip()
                except Exception:
                    pass

            if not text or is_bloat(text):
                continue
            records.append({
                "id": r["id"],
                "type": "feed",
                "author": r.get("author_name") or "School",
                "header": r.get("header"),
                "text": text,
                "timestamp": r.get("item_timestamp"),
            })

        # Messages
        for r in self.db.get_all_messages():
            body = (r.get("body") or "").strip()
            if not body or is_bloat(body):
                continue
            sender = r.get("sender_name") or "Teacher"
            records.append({
                "id": r["id"],
                "type": "message",
                "author": sender,
                "header": f"Direct message from {sender}",
                "text": body,
                "timestamp": r.get("message_timestamp"),
            })

        # Events
        for r in self.db.get_all_events():
            title = r.get("title") or "School Event"
            records.append({
                "id": r["id"],
                "type": "event",
                "author": "School Calendar",
                "header": title,
                "text": f"{title}: {r.get('description') or ''} (Start: {r.get('start_time') or 'Upcoming'})",
                "timestamp": r.get("start_time"),
            })

        return records

    def _rank_records(self, query: str, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compute relevance scores for records against the query."""
        query_words = set(re.findall(r"\w+", query))
        # Filter out common stop words
        stop_words = {"what", "when", "where", "how", "who", "is", "are", "there", "any", "the", "a", "an", "for", "to", "in", "of", "about", "does", "he", "she", "they", "my", "kid", "child"}
        keywords = query_words - stop_words
        if not keywords:
            keywords = query_words

        # Check for intent expansion
        expanded_keywords = set(keywords)
        for intent, related_words in INTENT_KEYWORDS.items():
            if any(k in query for k in [intent, *related_words[:3]]):
                expanded_keywords.update(related_words)

        scored = []
        now = datetime.now().astimezone()

        for rec in records:
            content = f"{rec.get('header', '')} {rec.get('text', '')}".lower()
            author = rec.get("author", "").lower()
            score = 0.0
            matched = False

            # Match exact phrase
            if len(query) > 5 and query in content:
                score += 50.0
                matched = True

            # Keyword matches
            for kw in keywords:
                if kw in content:
                    score += 10.0
                    matched = True
                if kw in author:
                    score += 5.0
                    matched = True

            # Expanded intent matches
            for ekw in expanded_keywords:
                if ekw in content:
                    score += 3.0
                    matched = True

            # If no keywords matched, do not include this record
            if not matched:
                continue

            # Recency bonus: reward posts from the last 7 days
            raw_time = rec.get("timestamp")
            if raw_time:
                try:
                    dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00")).astimezone()
                    days_old = (now.date() - dt.date()).days
                    if days_old <= 1:
                        score += 8.0
                    elif days_old <= 5:
                        score += 4.0
                    elif days_old <= 14:
                        score += 1.0
                except Exception:
                    pass

            if score > 5.0:
                scored.append((score, rec))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored]

    def _synthesize_answer(self, query: str, top_records: List[Dict[str, Any]]) -> str:
        """Format an answer answering the user's question directly from the top matches."""
        bullets = []
        for rec in top_records[:4]:
            author = rec.get("author") or "Teacher"
            posted_str, _ = parse_and_format_timestamp(rec.get("timestamp"))
            snippet = (rec.get("text") or "").strip()

            # Find the most relevant sentence in the snippet
            sentence = self._extract_best_sentence(query, snippet)
            bullets.append(f"• **{sentence}**\n  *(Source: {author} · {posted_str})*")

        answer_intro = self._get_answer_intro(query)
        return f"{answer_intro}\n\n" + "\n\n".join(bullets)

    def _extract_best_sentence(self, query: str, text: str) -> str:
        sentences = re.split(r"[.!?\n]+", text)
        query_words = set(re.findall(r"\w+", query.lower()))
        best_sentence = ""
        best_score = -1

        for s in sentences:
            clean = s.strip()
            if len(clean) < 10:
                continue
            lower = clean.lower()
            score = sum(1 for w in query_words if w in lower)
            if score > best_score:
                best_score = score
                best_sentence = clean

        return best_sentence or text.strip().split("\n")[0][:150]

    def _get_answer_intro(self, query: str) -> str:
        if "homework" in query:
            return "Here is the latest information regarding homework and assignments:"
        elif any(w in query for w in ["bring", "wear", "shoes", "supplies"]):
            return "Here are the items mentioned in recent classroom notices:"
        elif any(w in query for w in ["test", "fast", "assessment"]):
            return "Here is what the teacher posted about upcoming tests and assessments:"
        elif any(w in query for w in ["when", "date", "holiday", "off"]):
            return "Here are the upcoming dates and schedule notes from the school:"
        elif "sight word" in query:
            return "Here are the details for this week's sight words and reading practice:"
        return "Based on your classroom posts and messages, here is what I found:"

    def _generate_followups(self, query: str) -> List[str]:
        suggestions = []
        if "homework" in query:
            suggestions.extend(["What are this week's sight words?", "When is the next test?"])
        elif any(w in query for w in ["bring", "wear", "pack"]):
            suggestions.extend(["Is there homework tonight?", "When is the next field trip?"])
        elif any(w in query for w in ["test", "fast"]):
            suggestions.extend(["Is there math homework tonight?", "What does he need to bring?"])
        else:
            suggestions.extend(["What does he need to bring this week?", "Is there homework tonight?"])
        return suggestions[:2]

"""SQLite Database persistence layer for ClassDojo data."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DojoDatabase:
    """Handles local SQLite storage for ClassDojo children, classes, feeds, and digests."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS children (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    grade TEXT,
                    avatar_url TEXT,
                    updated_at TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS classes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    teacher_name TEXT,
                    child_id TEXT,
                    updated_at TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS feed_items (
                    id TEXT PRIMARY KEY,
                    story_id TEXT,
                    class_id TEXT,
                    child_id TEXT,
                    author_name TEXT,
                    header TEXT,
                    content_text TEXT,
                    attachments_json TEXT,
                    item_timestamp TEXT,
                    fetched_at TEXT NOT NULL,
                    digested_at TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT,
                    sender_id TEXT,
                    sender_name TEXT,
                    body TEXT,
                    message_timestamp TEXT,
                    fetched_at TEXT NOT NULL,
                    digested_at TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    class_id TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    location TEXT,
                    fetched_at TEXT NOT NULL,
                    digested_at TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS digests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    content_html TEXT NOT NULL,
                    content_text TEXT NOT NULL,
                    recipient_email TEXT,
                    item_count INTEGER DEFAULT 0,
                    sent_status TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT NOT NULL,
                    item_type TEXT NOT NULL,
                    sender_or_author TEXT,
                    title TEXT,
                    reason TEXT NOT NULL,
                    urgency_level TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    recipient_email TEXT,
                    sent_status TEXT NOT NULL
                )
            """)

            # Indexes for querying undigested and alerted records
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_feed_digested ON feed_items(digested_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_msg_digested ON messages(digested_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_digested ON events(digested_at)")
            cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_item ON alerts(item_id)")
            conn.commit()

    def upsert_child(self, child_id: str, name: str, grade: Optional[str] = None, avatar_url: Optional[str] = None) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO children (id, name, grade, avatar_url, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    grade = coalesce(excluded.grade, children.grade),
                    avatar_url = coalesce(excluded.avatar_url, children.avatar_url),
                    updated_at = excluded.updated_at
            """, (child_id, name, grade, avatar_url, utc_now_iso()))
            conn.commit()

    def upsert_class(self, class_id: str, name: str, teacher_name: Optional[str] = None, child_id: Optional[str] = None) -> None:
        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO classes (id, name, teacher_name, child_id, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    teacher_name = coalesce(excluded.teacher_name, classes.teacher_name),
                    child_id = coalesce(excluded.child_id, classes.child_id),
                    updated_at = excluded.updated_at
            """, (class_id, name, teacher_name, child_id, utc_now_iso()))
            conn.commit()

    def upsert_feed_items(self, items: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        Upsert feed items into the database.
        Returns: (new_items_count, updated_items_count)
        """
        new_count = 0
        updated_count = 0
        now = utc_now_iso()

        with self.get_connection() as conn:
            for item in items:
                item_id = str(item.get("id") or item.get("_id"))
                if not item_id:
                    continue

                # Check if exists
                cur = conn.execute("SELECT id FROM feed_items WHERE id = ?", (item_id,))
                exists = cur.fetchone() is not None

                attachments = item.get("attachments") or item.get("contents", {}).get("attachments") or []
                attachments_str = json.dumps(attachments)

                content_text = item.get("content_text") or item.get("contents", {}).get("body") or ""
                header = item.get("header") or item.get("contents", {}).get("title") or ""
                
                sender_name = item.get("senderName") or item.get("headerText") or item.get("author_name") or item.get("author", {}).get("name") or ""
                subtext = item.get("headerSubtext") or item.get("targetName") or ""
                if sender_name and subtext and subtext != sender_name:
                    author = f"{sender_name} ({subtext})"
                else:
                    author = sender_name or subtext or ""

                item_time = item.get("item_timestamp") or item.get("time") or item.get("createdAt")

                if exists:
                    conn.execute("""
                        UPDATE feed_items SET
                            story_id = coalesce(?, story_id),
                            class_id = coalesce(?, class_id),
                            child_id = coalesce(?, child_id),
                            author_name = coalesce(?, author_name),
                            header = coalesce(?, header),
                            content_text = ?,
                            attachments_json = ?,
                            item_timestamp = coalesce(?, item_timestamp)
                        WHERE id = ?
                    """, (
                        item.get("story_id"),
                        item.get("class_id"),
                        item.get("child_id"),
                        author,
                        header,
                        content_text,
                        attachments_str,
                        item_time,
                        item_id
                    ))
                    updated_count += 1
                else:
                    conn.execute("""
                        INSERT INTO feed_items (
                            id, story_id, class_id, child_id, author_name,
                            header, content_text, attachments_json, item_timestamp,
                            fetched_at, digested_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """, (
                        item_id,
                        item.get("story_id"),
                        item.get("class_id"),
                        item.get("child_id"),
                        author,
                        header,
                        content_text,
                        attachments_str,
                        item_time,
                        now
                    ))
                    new_count += 1
            conn.commit()

        return new_count, updated_count

    def upsert_messages(self, messages: List[Dict[str, Any]]) -> Tuple[int, int]:
        new_count = 0
        updated_count = 0
        now = utc_now_iso()

        with self.get_connection() as conn:
            for msg in messages:
                msg_id = str(msg.get("id") or msg.get("_id"))
                if not msg_id:
                    continue

                cur = conn.execute("SELECT id FROM messages WHERE id = ?", (msg_id,))
                exists = cur.fetchone() is not None

                body = msg.get("body") or msg.get("text") or ""
                sender_name = msg.get("sender_name") or msg.get("sender", {}).get("name") or ""
                sender_id = msg.get("sender_id") or msg.get("sender", {}).get("id") or ""
                time_val = msg.get("message_timestamp") or msg.get("time") or msg.get("createdAt")

                if exists:
                    conn.execute("""
                        UPDATE messages SET
                            conversation_id = coalesce(?, conversation_id),
                            sender_id = coalesce(?, sender_id),
                            sender_name = coalesce(?, sender_name),
                            body = ?,
                            message_timestamp = coalesce(?, message_timestamp)
                        WHERE id = ?
                    """, (msg.get("conversation_id"), sender_id, sender_name, body, time_val, msg_id))
                    updated_count += 1
                else:
                    conn.execute("""
                        INSERT INTO messages (
                            id, conversation_id, sender_id, sender_name,
                            body, message_timestamp, fetched_at, digested_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                    """, (msg_id, msg.get("conversation_id"), sender_id, sender_name, body, time_val, now))
                    new_count += 1
            conn.commit()

        return new_count, updated_count

    def upsert_events(self, events: List[Dict[str, Any]]) -> Tuple[int, int]:
        new_count = 0
        updated_count = 0
        now = utc_now_iso()

        with self.get_connection() as conn:
            for ev in events:
                ev_id = str(ev.get("id") or ev.get("_id"))
                if not ev_id:
                    continue

                cur = conn.execute("SELECT id FROM events WHERE id = ?", (ev_id,))
                exists = cur.fetchone() is not None

                title = ev.get("title") or ev.get("name") or "School Event"
                desc = ev.get("description") or ""
                start_time = ev.get("start_time") or ev.get("startsAt") or ev.get("date")
                end_time = ev.get("end_time") or ev.get("endsAt")
                loc = ev.get("location") or ""

                if exists:
                    conn.execute("""
                        UPDATE events SET
                            class_id = coalesce(?, class_id),
                            title = ?,
                            description = ?,
                            start_time = coalesce(?, start_time),
                            end_time = coalesce(?, end_time),
                            location = ?
                        WHERE id = ?
                    """, (ev.get("class_id"), title, desc, start_time, end_time, loc, ev_id))
                    updated_count += 1
                else:
                    conn.execute("""
                        INSERT INTO events (
                            id, class_id, title, description, start_time,
                            end_time, location, fetched_at, digested_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """, (ev_id, ev.get("class_id"), title, desc, start_time, end_time, loc, now))
                    new_count += 1
            conn.commit()

        return new_count, updated_count

    def get_undigested_items(self) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch all feed items, messages, and events that haven't been digested yet."""
        with self.get_connection() as conn:
            feed_rows = conn.execute("""
                SELECT f.*, c.name as class_name, ch.name as child_name
                FROM feed_items f
                LEFT JOIN classes c ON f.class_id = c.id
                LEFT JOIN children ch ON f.child_id = ch.id
                WHERE f.digested_at IS NULL
                ORDER BY f.item_timestamp DESC, f.fetched_at DESC
            """).fetchall()

            msg_rows = conn.execute("""
                SELECT * FROM messages
                WHERE digested_at IS NULL
                ORDER BY message_timestamp DESC, fetched_at DESC
            """).fetchall()

            event_rows = conn.execute("""
                SELECT e.*, c.name as class_name
                FROM events e
                LEFT JOIN classes c ON e.class_id = c.id
                WHERE e.digested_at IS NULL
                ORDER BY e.start_time ASC, e.fetched_at DESC
            """).fetchall()

            return {
                "feed_items": [dict(r) for r in feed_rows],
                "messages": [dict(r) for r in msg_rows],
                "events": [dict(r) for r in event_rows],
            }

    def mark_items_digested(
        self,
        feed_ids: List[str],
        message_ids: List[str],
        event_ids: List[str]
    ) -> None:
        """Mark items with the current timestamp so they are not included in future digests."""
        now = utc_now_iso()
        with self.get_connection() as conn:
            if feed_ids:
                placeholders = ",".join("?" for _ in feed_ids)
                conn.execute(f"UPDATE feed_items SET digested_at = ? WHERE id IN ({placeholders})", [now, *feed_ids])
            if message_ids:
                placeholders = ",".join("?" for _ in message_ids)
                conn.execute(f"UPDATE messages SET digested_at = ? WHERE id IN ({placeholders})", [now, *message_ids])
            if event_ids:
                placeholders = ",".join("?" for _ in event_ids)
                conn.execute(f"UPDATE events SET digested_at = ? WHERE id IN ({placeholders})", [now, *event_ids])
            conn.commit()

    def record_digest(
        self,
        content_html: str,
        content_text: str,
        recipient_email: str,
        item_count: int,
        sent_status: str
    ) -> int:
        now = utc_now_iso()
        with self.get_connection() as conn:
            cur = conn.execute("""
                INSERT INTO digests (created_at, content_html, content_text, recipient_email, item_count, sent_status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (now, content_html, content_text, recipient_email, item_count, sent_status))
            conn.commit()
            return cur.lastrowid or 0

    def get_stats(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            children_count = conn.execute("SELECT COUNT(*) FROM children").fetchone()[0]
            classes_count = conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0]
            feed_total = conn.execute("SELECT COUNT(*) FROM feed_items").fetchone()[0]
            feed_undigested = conn.execute("SELECT COUNT(*) FROM feed_items WHERE digested_at IS NULL").fetchone()[0]
            msg_undigested = conn.execute("SELECT COUNT(*) FROM messages WHERE digested_at IS NULL").fetchone()[0]
            events_undigested = conn.execute("SELECT COUNT(*) FROM events WHERE digested_at IS NULL").fetchone()[0]
            last_sync_row = conn.execute("SELECT MAX(fetched_at) FROM feed_items").fetchone()
            last_sync = last_sync_row[0] if last_sync_row and last_sync_row[0] else None

            return {
                "children_count": children_count,
                "classes_count": classes_count,
                "feed_total": feed_total,
                "undigested_items": feed_undigested + msg_undigested + events_undigested,
                "undigested_feeds": feed_undigested,
                "undigested_messages": msg_undigested,
                "undigested_events": events_undigested,
                "last_sync": last_sync,
            }

    def is_already_alerted(self, item_id: str) -> bool:
        """Check if an item has already triggered an immediate alert."""
        with self.get_connection() as conn:
            cur = conn.execute("SELECT id FROM alerts WHERE item_id = ?", (str(item_id),))
            return cur.fetchone() is not None

    def record_alert(
        self,
        item_id: str,
        item_type: str,
        sender_or_author: str,
        title: str,
        reason: str,
        urgency_level: str,
        recipient_email: str,
        sent_status: str
    ) -> int:
        now = utc_now_iso()
        with self.get_connection() as conn:
            cur = conn.execute("""
                INSERT INTO alerts (
                    item_id, item_type, sender_or_author, title,
                    reason, urgency_level, sent_at, recipient_email, sent_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    sent_status = excluded.sent_status,
                    sent_at = excluded.sent_at
            """, (
                str(item_id), item_type, sender_or_author, title,
                reason, urgency_level, now, recipient_email, sent_status
            ))
            conn.commit()
            return cur.lastrowid or 0

    def get_unalerted_items(self) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch all messages and feed items that have not yet been evaluated for immediate alerts."""
        with self.get_connection() as conn:
            msg_rows = conn.execute("""
                SELECT m.*, ch.name as child_name
                FROM messages m
                LEFT JOIN children ch ON 1=1
                WHERE m.id NOT IN (SELECT item_id FROM alerts)
                ORDER BY m.message_timestamp DESC, m.fetched_at DESC
            """).fetchall()

            feed_rows = conn.execute("""
                SELECT f.*, c.name as class_name, ch.name as child_name
                FROM feed_items f
                LEFT JOIN classes c ON f.class_id = c.id
                LEFT JOIN children ch ON f.child_id = ch.id
                WHERE f.id NOT IN (SELECT item_id FROM alerts)
                ORDER BY f.item_timestamp DESC, f.fetched_at DESC
            """).fetchall()

            return {
                "messages": [dict(r) for r in msg_rows],
                "feed_items": [dict(r) for r in feed_rows],
            }

    def get_all_feed_items(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            if limit:
                rows = conn.execute(
                    "SELECT * FROM feed_items ORDER BY item_timestamp DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM feed_items ORDER BY item_timestamp DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_feed_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM feed_items WHERE id = ?", (item_id,)).fetchone()
            return dict(row) if row else None

    def get_all_messages(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            if limit:
                rows = conn.execute(
                    "SELECT * FROM messages ORDER BY message_timestamp DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages ORDER BY message_timestamp DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_all_events(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY start_time ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_children(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM children").fetchall()
            return [dict(r) for r in rows]

    def save_session(self, cookie_data: Dict[str, str]) -> None:
        pass

    def load_session(self) -> Optional[Dict[str, str]]:
        return None


def get_database(force_sqlite: bool = False):
    """Factory to return either FirestoreDojoDatabase or local DojoDatabase."""
    from dojo.config import settings

    if not force_sqlite and settings.use_firestore:
        from dojo.firestore_db import FirestoreDojoDatabase
        return FirestoreDojoDatabase(project=settings.google_cloud_project)

    return DojoDatabase(settings.dojo_db_path)


"""Cloud Firestore persistence layer for ClassDojo data."""

import json
import logging
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_firestore_client(project: Optional[str] = None):
    """Obtain a Firestore client, falling back to local gcloud credentials if needed."""
    from google.cloud import firestore
    from google.auth.exceptions import DefaultCredentialsError

    try:
        return firestore.Client(project=project)
    except DefaultCredentialsError:
        # Fallback for local development if application-default login was not run
        try:
            from google.oauth2.credentials import Credentials
            token = subprocess.check_output(["gcloud", "auth", "print-access-token"], timeout=10).decode().strip()
            creds = Credentials(token)
            return firestore.Client(project=project, credentials=creds)
        except Exception as e:
            logger.error(f"Failed to authenticate with gcloud fallback: {e}")
            raise


class FirestoreDojoDatabase:
    """Handles Cloud Firestore storage for ClassDojo children, classes, feeds, messages, events, and session."""

    def __init__(self, project: Optional[str] = None, client: Optional[Any] = None):
        self.project = project
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = get_firestore_client(self.project)
        return self._client

    # --- Collections ---
    @property
    def feed_col(self):
        return self.client.collection("dojo_feed_items")

    @property
    def msg_col(self):
        return self.client.collection("dojo_messages")

    @property
    def event_col(self):
        return self.client.collection("dojo_events")

    @property
    def child_col(self):
        return self.client.collection("dojo_children")

    @property
    def class_col(self):
        return self.client.collection("dojo_classes")

    @property
    def digest_col(self):
        return self.client.collection("dojo_digests")

    @property
    def alert_col(self):
        return self.client.collection("dojo_alerts")

    @property
    def config_col(self):
        return self.client.collection("dojo_config")

    # --- Children & Classes ---
    def upsert_child(self, child_id: str, name: str, grade: Optional[str] = None, avatar_url: Optional[str] = None) -> None:
        doc_ref = self.child_col.document(child_id)
        data = {
            "id": child_id,
            "name": name,
            "grade": grade,
            "avatar_url": avatar_url,
            "updated_at": utc_now_iso(),
        }
        doc_ref.set(data, merge=True)

    def upsert_class(self, class_id: str, name: str, teacher_name: Optional[str] = None, child_id: Optional[str] = None) -> None:
        doc_ref = self.class_col.document(class_id)
        data = {
            "id": class_id,
            "name": name,
            "teacher_name": teacher_name,
            "child_id": child_id,
            "updated_at": utc_now_iso(),
        }
        doc_ref.set(data, merge=True)

    # --- Feed Items ---
    def upsert_feed_items(self, items: List[Dict[str, Any]]) -> Tuple[int, int]:
        new_count = 0
        updated_count = 0
        now = utc_now_iso()

        for item in items:
            item_id = str(item.get("id") or item.get("_id"))
            if not item_id:
                continue

            doc_ref = self.feed_col.document(item_id)
            doc = doc_ref.get()

            attachments = item.get("attachments") or item.get("contents", {}).get("attachments") or []
            attachments_str = json.dumps(attachments) if not isinstance(attachments, str) else attachments

            content_text = item.get("content_text") or item.get("contents", {}).get("body") or ""
            header = item.get("header") or item.get("contents", {}).get("title") or ""

            sender_name = (
                item.get("senderName")
                or item.get("headerText")
                or item.get("author_name")
                or item.get("author", {}).get("name")
                or ""
            )
            subtext = item.get("headerSubtext") or item.get("targetName") or ""
            if sender_name and subtext and subtext != sender_name:
                author = f"{sender_name} ({subtext})"
            else:
                author = sender_name or subtext or ""

            item_time = item.get("item_timestamp") or item.get("time") or item.get("createdAt")

            data = {
                "id": item_id,
                "story_id": item.get("story_id"),
                "class_id": item.get("class_id"),
                "child_id": item.get("child_id"),
                "author_name": author,
                "header": header,
                "content_text": content_text,
                "attachments_json": attachments_str,
                "item_timestamp": item_time,
            }

            if doc.exists:
                doc_ref.set(data, merge=True)
                updated_count += 1
            else:
                data["fetched_at"] = now
                data["digested_at"] = None
                doc_ref.set(data)
                new_count += 1

        return new_count, updated_count

    # --- Messages ---
    def upsert_messages(self, messages: List[Dict[str, Any]]) -> Tuple[int, int]:
        new_count = 0
        updated_count = 0
        now = utc_now_iso()

        for msg in messages:
            msg_id = str(msg.get("id") or msg.get("_id"))
            if not msg_id:
                continue

            doc_ref = self.msg_col.document(msg_id)
            doc = doc_ref.get()

            body = msg.get("body") or msg.get("text") or ""
            sender_name = msg.get("sender_name") or msg.get("sender", {}).get("name") or ""
            sender_id = msg.get("sender_id") or msg.get("sender", {}).get("id") or ""
            time_val = msg.get("message_timestamp") or msg.get("time") or msg.get("createdAt")

            data = {
                "id": msg_id,
                "conversation_id": msg.get("conversation_id"),
                "sender_id": sender_id,
                "sender_name": sender_name,
                "body": body,
                "message_timestamp": time_val,
            }

            if doc.exists:
                doc_ref.set(data, merge=True)
                updated_count += 1
            else:
                data["fetched_at"] = now
                data["digested_at"] = None
                doc_ref.set(data)
                new_count += 1

        return new_count, updated_count

    # --- Events ---
    def upsert_events(self, events: List[Dict[str, Any]]) -> Tuple[int, int]:
        new_count = 0
        updated_count = 0
        now = utc_now_iso()

        for ev in events:
            ev_id = str(ev.get("id") or ev.get("_id"))
            if not ev_id:
                continue

            doc_ref = self.event_col.document(ev_id)
            doc = doc_ref.get()

            title = ev.get("title") or ev.get("name") or "School Event"
            desc = ev.get("description") or ""
            start_time = ev.get("start_time") or ev.get("startsAt") or ev.get("date")
            end_time = ev.get("end_time") or ev.get("endsAt")
            loc = ev.get("location") or ""

            data = {
                "id": ev_id,
                "class_id": ev.get("class_id"),
                "title": title,
                "description": desc,
                "start_time": start_time,
                "end_time": end_time,
                "location": loc,
            }

            if doc.exists:
                doc_ref.set(data, merge=True)
                updated_count += 1
            else:
                data["fetched_at"] = now
                data["digested_at"] = None
                doc_ref.set(data)
                new_count += 1

        return new_count, updated_count

    # --- Queries for Briefings & Undigested Items ---
    def get_undigested_items(self) -> Dict[str, List[Dict[str, Any]]]:
        feed_docs = self.feed_col.where("digested_at", "==", None).stream()
        msg_docs = self.msg_col.where("digested_at", "==", None).stream()
        event_docs = self.event_col.where("digested_at", "==", None).stream()

        feed_items = [d.to_dict() for d in feed_docs]
        messages = [d.to_dict() for d in msg_docs]
        events = [d.to_dict() for d in event_docs]

        feed_items.sort(key=lambda x: (x.get("item_timestamp") or "", x.get("fetched_at") or ""), reverse=True)
        messages.sort(key=lambda x: (x.get("message_timestamp") or "", x.get("fetched_at") or ""), reverse=True)
        events.sort(key=lambda x: x.get("start_time") or "")

        return {
            "feed_items": feed_items,
            "messages": messages,
            "events": events,
        }

    def mark_items_digested(
        self,
        feed_ids: List[str],
        message_ids: List[str],
        event_ids: List[str]
    ) -> None:
        now = utc_now_iso()
        batch = self.client.batch()
        count = 0

        for fid in feed_ids:
            batch.update(self.feed_col.document(fid), {"digested_at": now})
            count += 1
            if count >= 450:
                batch.commit()
                batch = self.client.batch()
                count = 0

        for mid in message_ids:
            batch.update(self.msg_col.document(mid), {"digested_at": now})
            count += 1
            if count >= 450:
                batch.commit()
                batch = self.client.batch()
                count = 0

        for eid in event_ids:
            batch.update(self.event_col.document(eid), {"digested_at": now})
            count += 1
            if count >= 450:
                batch.commit()
                batch = self.client.batch()
                count = 0

        if count > 0:
            batch.commit()

    def record_digest(
        self,
        content_html: str,
        content_text: str,
        recipient_email: str,
        item_count: int,
        sent_status: str
    ) -> int:
        now = utc_now_iso()
        doc_ref = self.digest_col.document()
        doc_ref.set({
            "id": doc_ref.id,
            "created_at": now,
            "content_html": content_html,
            "content_text": content_text,
            "recipient_email": recipient_email,
            "item_count": item_count,
            "sent_status": sent_status,
        })
        return 1

    # --- Urgent Alerts ---
    def is_already_alerted(self, item_id: str) -> bool:
        doc = self.alert_col.document(str(item_id)).get()
        return doc.exists

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
        doc_ref = self.alert_col.document(str(item_id))
        doc_ref.set({
            "item_id": str(item_id),
            "item_type": item_type,
            "sender_or_author": sender_or_author,
            "title": title,
            "reason": reason,
            "urgency_level": urgency_level,
            "sent_at": now,
            "recipient_email": recipient_email,
            "sent_status": sent_status,
        }, merge=True)
        return 1

    def get_unalerted_items(self) -> Dict[str, List[Dict[str, Any]]]:
        alert_docs = self.alert_col.stream()
        alerted_ids = {d.id for d in alert_docs}

        msg_docs = self.msg_col.stream()
        feed_docs = self.feed_col.stream()

        unalerted_msgs = [d.to_dict() for d in msg_docs if d.id not in alerted_ids]
        unalerted_feeds = [d.to_dict() for d in feed_docs if d.id not in alerted_ids]

        unalerted_msgs.sort(key=lambda x: (x.get("message_timestamp") or "", x.get("fetched_at") or ""), reverse=True)
        unalerted_feeds.sort(key=lambda x: (x.get("item_timestamp") or "", x.get("fetched_at") or ""), reverse=True)

        return {
            "messages": unalerted_msgs,
            "feed_items": unalerted_feeds,
        }

    # --- Unified Query Methods for Web UI & Q&A ---
    def get_all_feed_items(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        docs = self.feed_col.stream()
        items = [d.to_dict() for d in docs]
        items.sort(key=lambda x: (x.get("item_timestamp") or "", x.get("fetched_at") or ""), reverse=True)
        return items[:limit] if limit else items

    def get_all_messages(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        docs = self.msg_col.stream()
        items = [d.to_dict() for d in docs]
        items.sort(key=lambda x: (x.get("message_timestamp") or "", x.get("fetched_at") or ""), reverse=True)
        return items[:limit] if limit else items

    def get_feed_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        doc = self.feed_col.document(str(item_id)).get()
        return doc.to_dict() if doc.exists else None

    def update_item_ocr(self, item_id: str, ocr_data: Any) -> None:
        """Persist OCR extraction results for a feed item in Firestore."""
        doc_ref = self.feed_col.document(str(item_id))
        doc_ref.set({"ocr_data": ocr_data}, merge=True)

    def get_all_events(self) -> List[Dict[str, Any]]:
        docs = self.event_col.stream()
        items = [d.to_dict() for d in docs]
        items.sort(key=lambda x: x.get("start_time") or "")
        return items

    def get_all_children(self) -> List[Dict[str, Any]]:
        docs = self.child_col.stream()
        return [d.to_dict() for d in docs]

    def get_all_classes(self) -> List[Dict[str, Any]]:
        docs = self.class_col.stream()
        return [d.to_dict() for d in docs]

    def get_stats(self) -> Dict[str, Any]:
        feed_items = self.get_all_feed_items()
        messages = self.get_all_messages()
        events = self.get_all_events()
        children = self.get_all_children()

        feed_undigested = sum(1 for f in feed_items if not f.get("digested_at"))
        msg_undigested = sum(1 for m in messages if not m.get("digested_at"))
        events_undigested = sum(1 for e in events if not e.get("digested_at"))

        last_sync = max((f.get("fetched_at") for f in feed_items if f.get("fetched_at")), default=None)

        return {
            "children_count": len(children),
            "classes_count": 0,
            "feed_total": len(feed_items),
            "undigested_items": feed_undigested + msg_undigested + events_undigested,
            "undigested_feeds": feed_undigested,
            "undigested_messages": msg_undigested,
            "undigested_events": events_undigested,
            "last_sync": last_sync,
            "backend": "firestore",
        }

    # --- Session Storage in Firestore ---
    def save_session(self, cookie_data: Dict[str, str]) -> None:
        self.config_col.document("session").set({
            "cookies": cookie_data,
            "updated_at": utc_now_iso(),
        })

    def load_session(self) -> Optional[Dict[str, str]]:
        doc = self.config_col.document("session").get()
        if doc.exists:
            data = doc.to_dict()
            return data.get("cookies")
        return None

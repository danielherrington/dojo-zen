"""Real-time monitoring and urgency alerting daemon."""

import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from rich.console import Console

from dojo.config import Settings
from dojo.db import DojoDatabase
from dojo.client import DojoClient
from dojo.alert_evaluator import AlertEvaluator, AlertDecision, UrgencyLevel
from dojo.mailer import Mailer

logger = logging.getLogger(__name__)
console = Console()

# A failed emergency alert is retried on each later check until it is sent, it has been
# tried this many times, or the underlying message is older than the retry window.
MAX_ALERT_ATTEMPTS = 5
ALERT_RETRY_WINDOW = timedelta(hours=24)


def _parse_item_time(raw: Any) -> Optional[datetime]:
    """Parse a stored ISO timestamp into an aware UTC datetime (None if missing/unparseable)."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class MessageMonitor:
    """Monitors incoming ClassDojo messages and announcements, sending immediate alerts for emergencies."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        db: Optional[Any] = None,
        client: Optional[DojoClient] = None
    ):
        from dojo.config import settings as default_settings
        from dojo.db import get_database

        self.settings = settings or default_settings
        self.db = db or get_database()
        self.client = client
        self.evaluator = AlertEvaluator(gemini_api_key=self.settings.gemini_api_key)
        self.mailer = Mailer(self.settings, db=self.db)

    def check_once(
        self,
        send_email: bool = True,
        to_email: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> List[AlertDecision]:
        """
        Sync latest items from ClassDojo, retry emergency alerts that previously failed to send,
        evaluate un-alerted items, and dispatch immediate notifications for any urgent messages.

        Each returned IMMEDIATE decision carries ``delivery_status`` ("sent", "failed: ...",
        or "dry_run") so callers can tell delivered emergencies from failed ones.
        """
        recipient = to_email or self.settings.email_to
        immediate_alerts: List[AlertDecision] = []

        # 1. Sync from ClassDojo if client or session exists
        try:
            client = self.client or DojoClient(session_file=self.settings.dojo_session_file)
            if client.is_authenticated():
                messages = client.get_messages()
                self.db.upsert_messages(messages)

                feed = client.get_story_feed(limit=20)
                self.db.upsert_feed_items(feed)

                events = client.get_events()
                self.db.upsert_events(events)
        except Exception as e:
            logger.warning(f"Warning during monitor sync: {e}")

        # 2. Retry emergency alerts whose earlier send failed (e.g. transient SMTP error)
        if send_email and recipient:
            immediate_alerts.extend(self._retry_failed_alerts(recipient, now=now))

        # 3. Query items that haven't been evaluated for immediate alerts yet
        unalerted = self.db.get_unalerted_items()
        messages = unalerted["messages"]
        feed_items = unalerted["feed_items"]

        # Evaluate messages
        for msg in messages:
            decision = self.evaluator.evaluate_message(msg)
            self._handle_decision(decision, send_email=send_email, recipient=recipient)
            if decision.urgency == UrgencyLevel.IMMEDIATE:
                immediate_alerts.append(decision)

        # Evaluate feed items
        for item in feed_items:
            decision = self.evaluator.evaluate_feed_item(item)
            self._handle_decision(decision, send_email=send_email, recipient=recipient)
            if decision.urgency == UrgencyLevel.IMMEDIATE:
                immediate_alerts.append(decision)

        return immediate_alerts

    def _retry_failed_alerts(self, recipient: str, now: Optional[datetime] = None) -> List[AlertDecision]:
        """Re-send IMMEDIATE alerts that failed earlier, within the attempt cap and freshness window."""
        get_failed = getattr(self.db, "get_failed_alerts", None)
        if get_failed is None:
            return []
        effective_now = now or datetime.now(timezone.utc)
        if effective_now.tzinfo is None:
            effective_now = effective_now.replace(tzinfo=timezone.utc)

        retried: List[AlertDecision] = []
        failed = get_failed(max_attempts=MAX_ALERT_ATTEMPTS)
        candidates = [("message", m) for m in failed.get("messages", [])] + [
            ("feed", f) for f in failed.get("feed_items", [])
        ]
        for item_type, item in candidates:
            raw_time = item.get("message_timestamp") if item_type == "message" else item.get("item_timestamp")
            item_time = _parse_item_time(raw_time)
            if item_time is not None and effective_now - item_time > ALERT_RETRY_WINDOW:
                continue  # a day-old emergency is no longer actionable; leave it for the recap

            if item_type == "message":
                decision = self.evaluator.evaluate_message(item)
            else:
                decision = self.evaluator.evaluate_feed_item(item)
            decision.is_retry = True
            logger.info(
                "Retrying failed urgent alert %s (attempt %s)",
                decision.item_id, int(item.get("alert_attempts") or 1) + 1,
            )
            self._handle_decision(decision, send_email=True, recipient=recipient, retry=True)
            if decision.urgency == UrgencyLevel.IMMEDIATE:
                retried.append(decision)
        return retried

    def _handle_decision(
        self,
        decision: AlertDecision,
        send_email: bool,
        recipient: Optional[str],
        retry: bool = False,
    ) -> None:
        """Process an alert decision: dispatch if urgent, record status in the alert log."""
        if not retry and self.db.is_already_alerted(decision.item_id):
            return
        if not retry:
            from dojo.usage_v1 import record_triage
            record_triage(self.db, decision)  # DAN-233: category + false-alarm guard counters, no text

        if decision.urgency == UrgencyLevel.IMMEDIATE:
            # Cloud Run stdout is Cloud Logging: log ids and category only, never the message text.
            from dojo.auth import is_cloud_run
            if is_cloud_run():
                logger.info("Urgent alert detected: item=%s category=%s", decision.item_id, decision.category)
            else:
                console.print(f"[bold red]🚨 URGENT ALERT DETECTED:[/bold red] {decision.title}")
                console.print(f"   [yellow]Reason:[/yellow] {decision.reason}")
                console.print(f"   [dim]Message: {decision.body[:120]}...[/dim]")

            status = "dry_run"
            if send_email and recipient:
                try:
                    self.mailer.send_urgent_alert(decision, to_email=recipient)
                    status = "sent"
                    console.print("[bold green]✓ Sent immediate priority alert.[/bold green]")
                except Exception as e:
                    status = f"failed: {e}"
                    logger.error("Failed to dispatch urgent alert %s: %s", decision.item_id, e)
                    console.print(f"[bold red]Failed to dispatch immediate alert: {e}[/bold red]")
            decision.delivery_status = status

            self.db.record_alert(
                item_id=decision.item_id,
                item_type=decision.item_type,
                sender_or_author=decision.sender_or_author,
                title=decision.title,
                reason=decision.reason,
                urgency_level=decision.urgency.value,
                recipient_email=recipient or "",
                sent_status=status
            )

        elif decision.urgency == UrgencyLevel.ROUTINE:
            # Routine item - recorded so we don't re-alert, but remains un-digested for daily recap!
            self.db.record_alert(
                item_id=decision.item_id,
                item_type=decision.item_type,
                sender_or_author=decision.sender_or_author,
                title=decision.title,
                reason=decision.reason,
                urgency_level=decision.urgency.value,
                recipient_email=recipient or "",
                sent_status="queued_for_recap"
            )

        else:
            # Bloat
            self.db.record_alert(
                item_id=decision.item_id,
                item_type=decision.item_type,
                sender_or_author=decision.sender_or_author,
                title=decision.title,
                reason=decision.reason,
                urgency_level=decision.urgency.value,
                recipient_email=recipient or "",
                sent_status="discarded_bloat"
            )

    def watch(self, poll_interval: int = 300, to_email: Optional[str] = None) -> None:
        """Run continuous monitoring loop."""
        console.print(f"[bold cyan]👀 Starting ClassDojo Alert Monitor daemon (polling every {poll_interval}s)...[/bold cyan]")
        console.print("[dim]Press Ctrl+C to stop.[/dim]\n")

        try:
            while True:
                alerts = self.check_once(send_email=True, to_email=to_email)
                if not alerts:
                    console.print(f"[dim]{time.strftime('%H:%M:%S')} - Checked ClassDojo: No urgent alerts.[/dim]")
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            console.print("\n[bold yellow]Monitor daemon stopped.[/bold yellow]")

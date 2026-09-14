"""Real-time monitoring and urgency alerting daemon."""

import time
import logging
from typing import Dict, List, Optional
from rich.console import Console

from dojo.config import Settings
from dojo.db import DojoDatabase
from dojo.client import DojoClient
from dojo.alert_evaluator import AlertEvaluator, AlertDecision, UrgencyLevel
from dojo.mailer import Mailer

logger = logging.getLogger(__name__)
console = Console()


class MessageMonitor:
    """Monitors incoming ClassDojo messages and announcements, sending immediate alerts for emergencies."""

    def __init__(self, settings: Settings, db: DojoDatabase):
        self.settings = settings
        self.db = db
        self.evaluator = AlertEvaluator(gemini_api_key=settings.gemini_api_key)
        self.mailer = Mailer(settings)

    def check_once(self, send_email: bool = True, to_email: Optional[str] = None) -> List[AlertDecision]:
        """
        Sync latest items from ClassDojo, evaluate un-alerted items,
        and dispatch immediate notifications for any urgent messages.
        """
        recipient = to_email or self.settings.email_to
        immediate_alerts: List[AlertDecision] = []

        # 1. Sync from ClassDojo if session exists
        if self.settings.dojo_session_file.exists():
            try:
                client = DojoClient(session_file=self.settings.dojo_session_file)
                # Fetch recent messages and feed items
                messages = client.get_messages()
                self.db.upsert_messages(messages)

                feed = client.get_story_feed(limit=20)
                self.db.upsert_feed_items(feed)
                client.close()
            except Exception as e:
                console.print(f"[dim yellow]Warning during monitor sync: {e}[/dim yellow]")

        # 2. Query items that haven't been evaluated for immediate alerts yet
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

    def _handle_decision(self, decision: AlertDecision, send_email: bool, recipient: Optional[str]) -> None:
        """Process an alert decision: dispatch if urgent, record status in SQLite."""
        if self.db.is_already_alerted(decision.item_id):
            return

        if decision.urgency == UrgencyLevel.IMMEDIATE:
            console.print(f"[bold red]🚨 URGENT ALERT DETECTED:[/bold red] {decision.title}")
            console.print(f"   [yellow]Reason:[/yellow] {decision.reason}")
            console.print(f"   [dim]Message: {decision.body[:120]}...[/dim]")

            status = "dry_run"
            if send_email and recipient:
                try:
                    self.mailer.send_urgent_alert(decision, to_email=recipient)
                    status = "sent"
                    console.print(f"[bold green]✓ Sent immediate priority alert to {recipient}![/bold green]")
                except Exception as e:
                    status = f"failed: {e}"
                    console.print(f"[bold red]Failed to dispatch immediate alert: {e}[/bold red]")

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

"""Email rendering and SMTP dispatching module."""

import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Optional
from jinja2 import Environment, FileSystemLoader

from dojo.config import Settings
from dojo.digest import Briefing


class Mailer:
    """Renders briefing into HTML/text email and sends via SMTP."""

    def __init__(self, settings: Settings):
        self.settings = settings
        templates_dir = Path(__file__).parent / "templates"
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=True
        )
        self.template = self.jinja_env.get_template("email_digest.html")
        self.alert_template = self.jinja_env.get_template("email_alert.html")

    def render_html(self, briefing: Briefing) -> str:
        """Render the Jinja2 HTML email template."""
        return self.template.render(briefing=briefing)

    def render_alert_html(self, decision: Any) -> str:
        """Render the Jinja2 HTML alert template."""
        return self.alert_template.render(decision=decision)

    def save_preview(self, html_content: str, output_path: Optional[Path] = None) -> Path:
        """Save rendered HTML digest to a local file for browser inspection."""
        path = output_path or Path("data/latest_digest.html")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_content, encoding="utf-8")
        return path

    def send_digest(
        self,
        briefing: Briefing,
        text_content: str,
        to_email: Optional[str] = None
    ) -> bool:
        """
        Send multipart (HTML + Text) digest email via configured SMTP.
        """
        raw_recipients = to_email or self.settings.email_to
        if not raw_recipients:
            raise ValueError("No recipient email provided (set EMAIL_TO in .env or pass --to).")

        # Parse multiple comma- or semicolon-separated recipients
        recipients = [r.strip() for r in re.split(r"[,;]+", raw_recipients) if r.strip()]
        if not recipients:
            raise ValueError("No valid recipient email addresses found.")

        if not self.settings.smtp_host or not self.settings.smtp_user:
            raise ValueError(
                "SMTP configuration missing. Please configure SMTP_HOST, SMTP_USER, "
                "and SMTP_PASS in your .env file."
            )

        html_content = self.render_html(briefing)

        # Build multipart message
        msg = MIMEMultipart("alternative")
        # Only prefix with [Urgent Action Items] if there are active, high-urgency items (e.g. today/due now)
        has_urgent_actions = any(a.urgency == "high" for a in briefing.active_action_items)
        subject_prefix = "🚨 [Urgent Actions] " if has_urgent_actions else ""
        period_str = f" {briefing.period}" if hasattr(briefing, "period") and briefing.period else ""
        msg["Subject"] = f"{subject_prefix}🎒 DojoZen{period_str} Briefing — {briefing.generated_at}"
        msg["From"] = self.settings.email_from or self.settings.smtp_user
        msg["To"] = ", ".join(recipients)

        part_text = MIMEText(text_content, "plain", "utf-8")
        part_html = MIMEText(html_content, "html", "utf-8")

        msg.attach(part_text)
        msg.attach(part_html)

        # Dispatch via SMTP
        if self.settings.smtp_port == 465:
            # SSL
            server = smtplib.SMTP_SSL(self.settings.smtp_host, self.settings.smtp_port, timeout=30)
        else:
            # TLS (e.g. port 587)
            server = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30)
            if self.settings.smtp_use_tls:
                server.starttls()

        try:
            if self.settings.smtp_user and self.settings.smtp_pass:
                server.login(self.settings.smtp_user, self.settings.smtp_pass)
            server.sendmail(self.settings.smtp_user, recipients, msg.as_string())
            return True
        finally:
            server.quit()

    def send_urgent_alert(self, decision: Any, to_email: Optional[str] = None) -> bool:
        """Send an immediate priority alert email for urgent events."""
        raw_recipients = to_email or self.settings.email_to
        if not raw_recipients:
            raise ValueError("No recipient email provided (set EMAIL_TO in .env or pass --to).")

        recipients = [r.strip() for r in re.split(r"[,;]+", raw_recipients) if r.strip()]
        if not recipients:
            raise ValueError("No valid recipient email addresses found.")

        if not self.settings.smtp_host or not self.settings.smtp_user:
            raise ValueError(
                "SMTP configuration missing. Please configure SMTP_HOST, SMTP_USER, "
                "and SMTP_PASS in your .env file."
            )

        html_content = self.render_alert_html(decision)
        plain_text = (
            f"🚨 URGENT DOJOZEN ALERT\n"
            f"=========================================\n"
            f"From: {decision.sender_or_author}\n"
            f"Reason: {decision.reason}\n"
            f"Message: {decision.body}\n"
        )
        if decision.action_required:
            plain_text += f"Action Required: {decision.action_required}\n"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🚨 URGENT DojoZen Alert: {decision.title}"
        msg["From"] = self.settings.email_from or self.settings.smtp_user
        msg["To"] = ", ".join(recipients)
        msg["X-Priority"] = "1"  # High priority header
        msg["Importance"] = "High"

        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        if self.settings.smtp_port == 465:
            server = smtplib.SMTP_SSL(self.settings.smtp_host, self.settings.smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30)
            if self.settings.smtp_use_tls:
                server.starttls()

        try:
            if self.settings.smtp_user and self.settings.smtp_pass:
                server.login(self.settings.smtp_user, self.settings.smtp_pass)
            server.sendmail(self.settings.smtp_user, recipients, msg.as_string())
            return True
        finally:
            server.quit()


def dispatch_daily_briefing(
    db: Any,
    settings: Optional[Settings] = None,
    force: bool = False,
    to_email: Optional[str] = None,
    dry_run: bool = False
) -> dict:
    """Compile and dispatch daily briefing email, marking items digested and recording audit log."""
    from dojo.config import settings as default_settings
    from dojo.digest import DigestEngine

    s = settings or default_settings
    data = db.get_undigested_items()
    feed_items = data["feed_items"]
    messages = data["messages"]
    events = data["events"]

    total_undigested = len(feed_items) + len(messages) + len(events)
    if total_undigested == 0:
        # If no new items arrived overnight or since last digest, compile recent items so morning/evening briefing always delivers
        feed_items = db.get_all_feed_items(limit=15) if hasattr(db, "get_all_feed_items") else []
        messages = db.get_all_messages(limit=10) if hasattr(db, "get_all_messages") else []
        events = db.get_all_events() if hasattr(db, "get_all_events") else []
        total_undigested = len(feed_items) + len(messages) + len(events)
        if total_undigested == 0:
            return {"status": "skipped", "reason": "No items found in database", "count": 0}

    children = db.get_all_children() if hasattr(db, "get_all_children") else []
    engine = DigestEngine(gemini_api_key=s.gemini_api_key)
    briefing = engine.synthesize(
        feed_items=feed_items,
        messages=messages,
        events=events,
        children=children
    )

    text_content = engine.format_plain_text(briefing)
    mailer = Mailer(s)

    if dry_run:
        html_preview = mailer.render_html(briefing)
        return {"status": "dry_run", "item_count": total_undigested, "preview_html": html_preview}

    email_sent = mailer.send_digest(briefing, text_content, to_email=to_email)
    html_content = mailer.render_html(briefing)

    recipient = to_email or s.email_to
    db.record_digest(
        content_html=html_content,
        content_text=text_content,
        recipient_email=recipient,
        item_count=total_undigested,
        sent_status="sent" if email_sent else "failed"
    )

    feed_ids = [f["id"] for f in feed_items if f.get("id")]
    message_ids = [m["id"] for m in messages if m.get("id")]
    event_ids = [e["id"] for e in events if e.get("id")]
    db.mark_items_digested(feed_ids, message_ids, event_ids)

    return {"status": "sent", "item_count": total_undigested, "recipient": recipient}


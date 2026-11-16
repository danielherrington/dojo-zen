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
        return self.template.render(
            briefing=briefing,
            app_base_url=self.settings.app_base_url.rstrip("/")
        )

    def render_alert_html(self, decision: Any) -> str:
        """Render the Jinja2 HTML alert template."""
        return self.alert_template.render(
            decision=decision,
            app_base_url=self.settings.app_base_url.rstrip("/")
        )

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


def is_digest_window_allowed(ref_dt: Optional[Any] = None) -> tuple[bool, str]:
    """
    Determine whether automated daily digest emails are allowed at ref_dt.
    - Saturday (all day): Skipped (no Saturday briefings).
    - Sunday before 4:00 PM (hour < 16): Skipped (first weekend briefing is Sunday evening).
    - Sunday evening (hour >= 16): Allowed (week-ahead / Monday prep briefing).
    - Monday–Friday: Allowed.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    effective_dt = ref_dt if isinstance(ref_dt, datetime) else datetime.now(ZoneInfo("America/New_York"))
    weekday = effective_dt.weekday()  # 0=Mon ... 5=Sat, 6=Sun

    if weekday == 5:
        return False, "Weekend quiet window (Saturday — briefings resume Sunday evening)"
    if weekday == 6 and effective_dt.hour < 16:
        return False, "Weekend quiet window (Sunday morning — briefings resume Sunday evening)"
    if weekday == 6:
        return True, "Sunday evening week-ahead briefing window"
    return True, "Weekday briefing window"


def dispatch_daily_briefing(
    db: Any,
    settings: Optional[Settings] = None,
    force: bool = False,
    to_email: Optional[str] = None,
    dry_run: bool = False,
    ref_dt: Optional[Any] = None,
) -> dict:
    """Compile and dispatch daily briefing email, enforcing weekend quiet windows and quiet-day skipping unless forced."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from dojo.config import settings as default_settings
    from dojo.digest import DigestEngine, collect_briefing_inputs, parse_raw_datetime

    s = settings or default_settings
    effective_ref = ref_dt if isinstance(ref_dt, datetime) else datetime.now(ZoneInfo("America/New_York"))

    # 1. Weekend schedule guardrail (never send Saturday or Sunday morning briefings unless forced;
    #    leave undigested items untouched so they roll into Sunday evening's briefing).
    if not force:
        allowed, window_reason = is_digest_window_allowed(effective_ref)
        if not allowed:
            return {"status": "skipped", "reason": window_reason, "count": 0}

    # 2. Check last sent digest to prevent duplicate same-day emails
    last_sent_dt: Optional[datetime] = None
    if not force and hasattr(db, "get_last_sent_digest"):
        last_digest = db.get_last_sent_digest()
        if last_digest and last_digest.get("created_at"):
            last_sent_dt = parse_raw_datetime(last_digest["created_at"])
            if last_sent_dt is not None and effective_ref.tzinfo is not None and last_sent_dt.tzinfo is not None:
                last_sent_dt = last_sent_dt.astimezone(effective_ref.tzinfo)
            if last_sent_dt is not None and last_sent_dt.date() == effective_ref.date():
                return {"status": "skipped", "reason": "Daily briefing already sent today", "count": 0}

    inputs = collect_briefing_inputs(db)
    undigested = inputs["undigested"]
    feed_items = inputs["feed_items"]
    messages = inputs["messages"]
    events = inputs["events"]
    children = inputs["children"]

    total_candidates = len(feed_items) + len(messages) + len(events)
    if total_candidates == 0 and not force:
        return {"status": "skipped", "reason": "No items found in database", "count": 0}

    engine = DigestEngine(gemini_api_key=s.gemini_api_key)
    briefing = engine.synthesize(
        feed_items=feed_items,
        messages=messages,
        events=events,
        children=children,
        ref_dt=effective_ref,
        daily_focus=True,
        respect_digested=True,
    )

    u_feed_ids = [str(f["id"]) for f in undigested.get("feed_items", []) if f.get("id")]
    u_msg_ids = [str(m["id"]) for m in undigested.get("messages", []) if m.get("id")]
    u_ev_ids = [str(e["id"]) for e in undigested.get("events", []) if e.get("id")]
    has_undigested = bool(u_feed_ids or u_msg_ids or u_ev_ids)

    if briefing.is_empty() and not force:
        if not dry_run and has_undigested:
            db.mark_items_digested(u_feed_ids, u_msg_ids, u_ev_ids)
        return {"status": "skipped", "reason": "No daily-relevant items for today", "count": 0}

    # 3. If Sunday evening already briefed Monday's items and nothing new arrived overnight (and no Tuesday lookahead items),
    #    avoid re-sending the exact same Monday reminder 13 hours later on Monday morning.
    if (
        not force
        and effective_ref.weekday() == 0
        and last_sent_dt is not None
        and last_sent_dt.date() == (effective_ref.date() - timedelta(days=1))
        and not has_undigested
    ):
        has_tuesday_lookahead = any(
            a.summary.lower().startswith("tomorrow") for a in briefing.active_action_items
        ) or any(
            d.title.lower().startswith("tomorrow") for d in briefing.active_upcoming_dates
        )
        if not has_tuesday_lookahead:
            return {"status": "skipped", "reason": "Already covered in Sunday evening briefing", "count": 0}

    daily_count = briefing.daily_item_count
    text_content = engine.format_plain_text(briefing)
    mailer = Mailer(s)

    if dry_run:
        html_preview = mailer.render_html(briefing)
        return {"status": "dry_run", "item_count": daily_count, "preview_html": html_preview}

    email_sent = mailer.send_digest(briefing, text_content, to_email=to_email)
    html_content = mailer.render_html(briefing)

    recipient = to_email or s.email_to
    db.record_digest(
        content_html=html_content,
        content_text=text_content,
        recipient_email=recipient,
        item_count=daily_count,
        sent_status="sent" if email_sent else "failed",
        created_at=effective_ref.isoformat(),
    )

    if has_undigested:
        db.mark_items_digested(u_feed_ids, u_msg_ids, u_ev_ids)

    return {"status": "sent", "item_count": daily_count, "recipient": recipient}


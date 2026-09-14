"""Email rendering and SMTP dispatching module."""

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
        recipient = to_email or self.settings.email_to
        if not recipient:
            raise ValueError("No recipient email provided (set EMAIL_TO in .env or pass --to).")

        if not self.settings.smtp_host or not self.settings.smtp_user:
            raise ValueError(
                "SMTP configuration missing. Please configure SMTP_HOST, SMTP_USER, "
                "and SMTP_PASS in your .env file."
            )

        html_content = self.render_html(briefing)

        # Build multipart message
        msg = MIMEMultipart("alternative")
        subject_prefix = "🚨 Action Items: " if briefing.action_items else ""
        msg["Subject"] = f"{subject_prefix}🎒 ClassDojo Daily Briefing — {briefing.generated_at}"
        msg["From"] = self.settings.email_from or self.settings.smtp_user
        msg["To"] = recipient

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
            server.sendmail(self.settings.smtp_user, [recipient], msg.as_string())
            return True
        finally:
            server.quit()

    def send_urgent_alert(self, decision: Any, to_email: Optional[str] = None) -> bool:
        """Send an immediate priority alert email for urgent events."""
        recipient = to_email or self.settings.email_to
        if not recipient:
            raise ValueError("No recipient email provided (set EMAIL_TO in .env or pass --to).")

        if not self.settings.smtp_host or not self.settings.smtp_user:
            raise ValueError(
                "SMTP configuration missing. Please configure SMTP_HOST, SMTP_USER, "
                "and SMTP_PASS in your .env file."
            )

        html_content = self.render_alert_html(decision)
        plain_text = (
            f"🚨 URGENT CLASSDOJO ALERT\n"
            f"=========================================\n"
            f"From: {decision.sender_or_author}\n"
            f"Reason: {decision.reason}\n"
            f"Message: {decision.body}\n"
        )
        if decision.action_required:
            plain_text += f"Action Required: {decision.action_required}\n"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🚨 URGENT ClassDojo Alert: {decision.title}"
        msg["From"] = self.settings.email_from or self.settings.smtp_user
        msg["To"] = recipient
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
            server.sendmail(self.settings.smtp_user, [recipient], msg.as_string())
            return True
        finally:
            server.quit()


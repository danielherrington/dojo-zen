"""Unit tests for the Mailer and HTML template rendering."""

import tempfile
from pathlib import Path
from dojo.config import Settings
from dojo.digest import Briefing, ActionItem, UpcomingDate, TeacherNote, ClassroomHighlight
from dojo.mailer import Mailer


def test_mailer_render_html():
    settings = Settings()
    mailer = Mailer(settings)

    briefing = Briefing(
        generated_at="Monday, Sep 14, 2026",
        children_names=["Leo"],
        action_items=[
            ActionItem(summary="Bring sneakers for gym", context="Posted by Ms. Jenkins", urgency="high")
        ],
        upcoming_dates=[
            UpcomingDate(title="Early Dismissal 12:30 PM", date_str="Friday, Sep 18", details="Teacher workday")
        ],
        teacher_notes=[
            TeacherNote(sender="Ms. Jenkins", body="Leo had a fantastic day working in groups.")
        ],
        classroom_highlights=[
            ClassroomHighlight(author="Art Teacher", text="Students painted fall landscapes.", attachment_count=4)
        ],
        raw_item_count=4,
        filtered_bloat_count=2
    )

    html = mailer.render_html(briefing)

    assert "DojoZen Daily Briefing" in html
    assert "Leo" in html
    assert "Bring sneakers for gym" in html
    assert "URGENT" in html
    assert "Early Dismissal 12:30 PM" in html
    assert "Ms. Jenkins" in html
    assert "Filtered out 2 promotional" in html


def test_mailer_save_preview():
    settings = Settings()
    mailer = Mailer(settings)
    briefing = Briefing(
        generated_at="Monday, Sep 14, 2026",
        children_names=["Maya"],
        raw_item_count=0,
        filtered_bloat_count=0
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        preview_file = Path(tmpdir) / "test_digest.html"
        html = mailer.render_html(briefing)
        saved = mailer.save_preview(html, output_path=preview_file)

        assert saved.exists()
        content = saved.read_text(encoding="utf-8")
        assert "All caught up!" in content


def test_mailer_multi_recipient(monkeypatch):
    """Verify that multiple comma-separated emails are correctly parsed and dispatched."""
    settings = Settings(
        smtp_host="localhost",
        smtp_user="test@example.com",
        smtp_pass="password",
        email_to="daniel.j.herrington@gmail.com, lucilatijman@gmail.com, mtijman@gmail.com"
    )
    mailer = Mailer(settings)
    briefing = Briefing(generated_at="Monday, Sep 14, 2026")

    sent_recipients = []

    class MockSMTP:
        def __init__(self, host, port, timeout=30):
            pass
        def starttls(self):
            pass
        def login(self, user, pwd):
            pass
        def sendmail(self, sender, to_addrs, msg_str):
            sent_recipients.extend(to_addrs)
            assert "To: daniel.j.herrington@gmail.com, lucilatijman@gmail.com, mtijman@gmail.com" in msg_str
        def quit(self):
            pass

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP", MockSMTP)

    result = mailer.send_digest(briefing, "Test text content")
    assert result is True
    assert sent_recipients == [
        "daniel.j.herrington@gmail.com",
        "lucilatijman@gmail.com",
        "mtijman@gmail.com"
    ]


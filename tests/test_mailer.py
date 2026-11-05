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


def test_mailer_digest_and_alert_links():
    settings = Settings(app_base_url="https://dojo-zen-test.run.app")
    mailer = Mailer(settings)

    briefing = Briefing(
        generated_at="Monday, Sep 14, 2026",
        action_items=[
            ActionItem(summary="Field trip permission slip", context="Permission needed", item_id="item123", image_urls=["https://img.com/slip.jpg"])
        ],
        upcoming_dates=[
            UpcomingDate(title="Science Fair", date_str="Oct 12", item_id="item456")
        ],
        teacher_notes=[
            TeacherNote(sender="Teacher A", body="Hello class", item_id="item789")
        ],
        classroom_highlights=[
            ClassroomHighlight(author="Art Teacher", text="Art project", item_id="item999")
        ]
    )

    html = mailer.render_html(briefing)

    # Must link to DojoZen, never ClassDojo
    assert "home.classdojo.com" not in html
    assert "https://dojo-zen-test.run.app" in html
    assert "https://dojo-zen-test.run.app/?item=item123#item-item123" in html
    assert "https://dojo-zen-test.run.app/?item=item456#item-item456" in html
    assert "https://dojo-zen-test.run.app/?item=item789#item-item789" in html
    assert "https://dojo-zen-test.run.app/?item=item999#item-item999" in html
    assert "Open DojoZen" in html

    # Alert rendering test
    from dojo.alert_evaluator import AlertDecision, UrgencyLevel
    alert = AlertDecision(
        item_id="alert123",
        item_type="message",
        sender_or_author="School Nurse",
        title="Leo in nurse office",
        body="Leo has a slight fever.",
        urgency=UrgencyLevel.IMMEDIATE,
        reason="Medical attention",
        action_required="Please pick up Leo"
    )

    alert_html = mailer.render_alert_html(alert)
    assert "home.classdojo.com" not in alert_html
    assert "https://dojo-zen-test.run.app/?item=alert123#item-alert123" in alert_html
    assert "View Notice in DojoZen ↗" in alert_html


def test_dispatch_daily_briefing_skips_quiet_day_and_surfaces_tomorrow_reminders(monkeypatch, tmp_path):
    """Verify quiet days skip sending email (instead of emailing old history), while Today/Tomorrow deadlines from earlier days still send."""
    from datetime import datetime, timezone
    from dojo.db import DojoDatabase
    from dojo.mailer import Mailer, dispatch_daily_briefing

    db = DojoDatabase(tmp_path / "test_daily_dispatch.db")
    custom_settings = Settings(
        smtp_host="localhost",
        smtp_user="test@example.com",
        smtp_pass="password",
        email_to="parent@example.com",
    )

    # Seed an already-digested Monday post announcing a Friday test + a far-future event + promotional bloat
    db.upsert_feed_items([
        {
            "id": "mon_post_for_fri",
            "author_name": "Ms. Alonso",
            "content_text": "Reminder: Spelling test on Friday! Practice words.",
            "item_timestamp": "2026-09-14T15:00:00Z",
        },
        {
            "id": "bloat_post",
            "author_name": "ClassDojo",
            "content_text": "Try ClassDojo Plus free trial today!",
            "item_timestamp": "2026-09-16T06:00:00Z",
        },
    ])
    db.mark_items_digested(["mon_post_for_fri"], [], [])
    db.upsert_events([
        {
            "id": "far_event",
            "title": "Winter Concert",
            "start_time": "2026-12-18",
            "description": "Auditorium",
        }
    ])
    db.mark_items_digested([], [], ["far_event"])

    sent_subjects = []

    def fake_send_digest(self, briefing, text_content, to_email=None):
        sent_subjects.append(briefing.generated_at)
        return True

    monkeypatch.setattr(Mailer, "send_digest", fake_send_digest)

    # 1. On Wednesday Sep 16 (nothing due Wed/Thu, only bloat arrived overnight) -> SKIPPED, bloat marked digested!
    wed_res = dispatch_daily_briefing(
        db=db,
        settings=custom_settings,
        force=False,
        ref_dt=datetime(2026, 9, 16, 11, 0, tzinfo=timezone.utc),
    )
    assert wed_res["status"] == "skipped"
    assert sent_subjects == []
    assert db.get_stats()["undigested_items"] == 0

    # 2. On Wednesday Sep 16 with force=True -> SENDS "All caught up" confirmation
    forced_res = dispatch_daily_briefing(
        db=db,
        settings=custom_settings,
        force=True,
        ref_dt=datetime(2026, 9, 16, 11, 0, tzinfo=timezone.utc),
    )
    assert forced_res["status"] == "sent"
    assert len(sent_subjects) == 1

    # 3. On Thursday Sep 17 (even with 0 undigested items, Monday's post has Friday's test = Tomorrow!) -> SENDS!
    thu_res = dispatch_daily_briefing(
        db=db,
        settings=custom_settings,
        force=False,
        ref_dt=datetime(2026, 9, 17, 11, 0, tzinfo=timezone.utc),
    )
    assert thu_res["status"] == "sent"
    assert thu_res["item_count"] >= 1
    assert len(sent_subjects) == 2



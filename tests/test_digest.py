"""Unit tests for the anti-bloat digest engine."""

from dojo.digest import DigestEngine, is_bloat


def test_is_bloat_detection():
    # Should detect promotional bloat
    assert is_bloat("Try ClassDojo Plus free for 7 days to unlock premium features!")
    assert is_bloat("Dress your monster in brand new winter jackets with Dojo Beyond!")
    assert is_bloat("Subscribe now to share moments with all family members.")

    # Should NOT flag regular school posts
    assert not is_bloat("Please remember to wear sneakers tomorrow for gym class.")
    assert not is_bloat("Early dismissal on Friday at 12:30 PM due to parent-teacher conferences.")
    assert not is_bloat("Great job today everyone, we read chapter 4 of Charlotte's Web.")


def test_synthesize_action_items_and_dates():
    engine = DigestEngine()

    feed_items = [
        {
            "id": "item_1",
            "author_name": "Ms. Miller",
            "content_text": "Please bring an empty shoe box tomorrow for our science project.",
            "item_timestamp": "2026-09-14T10:00:00Z"
        },
        {
            "id": "item_2",
            "author_name": "ClassDojo",
            "content_text": "Unlock all monsters and costumes with ClassDojo Plus! Start your free trial today.",
            "item_timestamp": "2026-09-14T11:00:00Z"
        },
        {
            "id": "item_3",
            "author_name": "School Office",
            "content_text": "Reminder: Early dismissal on Friday at 12:30 PM.",
            "item_timestamp": "2026-09-14T12:00:00Z"
        },
        {
            "id": "item_4",
            "author_name": "Ms. Miller",
            "content_text": "Today we had an awesome art session and painted watercolor trees.",
            "item_timestamp": "2026-09-14T13:00:00Z",
            "attachments_json": '[{"path": "http://img/1.jpg"}, {"path": "http://img/2.jpg"}]'
        }
    ]

    messages = [
        {
            "id": "msg_1",
            "sender_name": "Ms. Miller",
            "body": "Hi, just a reminder to sign and return the permission slip by Friday!",
            "message_timestamp": "2026-09-14T14:30:00Z"
        }
    ]

    events = [
        {
            "id": "ev_1",
            "title": "Fall Picture Day",
            "description": "Wear your best smile! Uniform optional.",
            "start_time": "2026-09-22"
        }
    ]

    children = [{"name": "Leo"}]

    briefing = engine.synthesize(feed_items, messages, events, children)

    # 1. Bloat filtered
    assert briefing.filtered_bloat_count == 1

    # 2. Action items captured
    assert len(briefing.action_items) >= 2
    action_summaries = " ".join([a.summary.lower() for a in briefing.action_items])
    assert "shoe box" in action_summaries
    assert "permission slip" in action_summaries

    # 3. Dates captured
    assert len(briefing.upcoming_dates) >= 2
    date_titles = " ".join([d.title.lower() for d in briefing.upcoming_dates])
    assert "early dismissal" in date_titles or "fall picture day" in date_titles

    # 4. Teacher note captured
    assert len(briefing.teacher_notes) == 1
    assert "Ms. Miller" in briefing.teacher_notes[0].sender

    # 5. Highlights captured
    assert len(briefing.classroom_highlights) == 1
    assert briefing.classroom_highlights[0].attachment_count == 2
    assert briefing.classroom_highlights[0].image_urls == ["http://img/1.jpg", "http://img/2.jpg"]

    # 6. Plain text formatting
    plain_text = engine.format_plain_text(briefing)
    assert "CLASSDOJO DAILY BRIEFING" in plain_text
    assert "ACTION ITEMS & TO-DOS" in plain_text
    assert "shoe box" in plain_text.lower()
    assert "Filtered out 1 marketing/bloat items" in plain_text

    # 7. HTML Email rendering contains images
    from dojo.config import settings
    from dojo.mailer import Mailer
    mailer = Mailer(settings)
    html = mailer.render_html(briefing)
    assert '<img src="http://img/1.jpg"' in html
    assert '<img src="http://img/2.jpg"' in html

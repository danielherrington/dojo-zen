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

    from datetime import datetime, timezone
    briefing = engine.synthesize(
        feed_items, messages, events, children,
        ref_dt=datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
    )

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
    assert "DOJOZEN DAILY BRIEFING" in plain_text
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


def test_synthesize_excludes_images_for_already_digested_items():
    """Verify that items marked as already digested do not re-send photos in future updates."""
    from datetime import datetime, timezone
    engine = DigestEngine()

    feed_items = [
        {
            "id": "item_fresh_action",
            "author_name": "Ms. Miller",
            "content_text": "Please remember to sign the form tomorrow.",
            "item_timestamp": "2026-09-17T10:00:00Z",
            "digested_at": None,
            "attachments_json": '[{"path": "http://img/fresh_form.jpg"}]'
        },
        {
            "id": "item_already_digested_action",
            "author_name": "Mr. Davis",
            "content_text": "Reminder: Bring library books on Friday!",
            "item_timestamp": "2026-09-16T12:00:00Z",
            "digested_at": "2026-09-16T17:00:00Z",
            "attachments_json": '[{"path": "http://img/old_library.jpg"}]'
        },
        {
            "id": "item_already_digested_highlight",
            "author_name": "Ms. Miller",
            "content_text": "Photos from Doughnuts with Dudes this morning!",
            "item_timestamp": "2026-09-16T09:00:00Z",
            "digested_at": "2026-09-16T17:00:00Z",
            "attachments_json": '[{"path": "http://img/dudes_1.jpg"}, {"path": "http://img/dudes_2.jpg"}]'
        }
    ]

    briefing = engine.synthesize(
        feed_items=feed_items, messages=[], events=[],
        ref_dt=datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    )

    # 1. Action items are retained for context, but old ones have no image_urls
    actions_by_summary = {a.summary.lower(): a for a in briefing.action_items}
    # Fresh action item keeps its photo
    assert any("sign the form" in k and a.image_urls == ["http://img/fresh_form.jpg"] for k, a in actions_by_summary.items())
    # Already-digested action item retains the action text, but strips the photo
    assert any("library books" in k and a.image_urls == [] for k, a in actions_by_summary.items())

    # 2. Already-digested photo gallery / highlight is completely omitted
    assert len(briefing.classroom_highlights) == 0

    # 3. Rendered HTML email only includes the fresh photo, none of the previously sent photos
    from dojo.config import settings
    from dojo.mailer import Mailer
    mailer = Mailer(settings)
    html = mailer.render_html(briefing)
    assert "fresh_form.jpg" in html
    assert "old_library.jpg" not in html
    assert "dudes_1.jpg" not in html


def test_contextualize_tomorrow_reminder_to_today():
    """Verify that 'wear green tomorrow' posted yesterday is rewritten to 'Today: ...' with high urgency."""
    from datetime import datetime, timezone
    engine = DigestEngine()

    feed_items = [
        {
            "id": "green_day",
            "author_name": "Ms. Miller",
            "content_text": "💚 Don't forget to wear GREEN tomorrow",
            "item_timestamp": "2026-09-22T20:48:00Z"  # Tuesday evening
        }
    ]

    # 1. On Wednesday morning (day of event):
    wednesday_morning = datetime(2026, 9, 23, 7, 0, tzinfo=timezone.utc)
    briefing_wed = engine.synthesize(feed_items=feed_items, messages=[], events=[], ref_dt=wednesday_morning)

    active_items = briefing_wed.active_action_items
    assert len(active_items) == 1
    item = active_items[0]
    assert item.urgency == "high"
    assert "Today: 💚 Don't forget to wear GREEN" in item.summary
    assert "tomorrow" not in item.summary.lower()

    # 2. On Thursday morning (day AFTER event):
    thursday_morning = datetime(2026, 9, 24, 7, 0, tzinfo=timezone.utc)
    briefing_thu = engine.synthesize(feed_items=feed_items, messages=[], events=[], ref_dt=thursday_morning)

    # Must be completely omitted from active action items and expired past notices
    assert len(briefing_thu.active_action_items) == 0
    assert len(briefing_thu.expired_action_items) == 0


def test_past_events_and_deadlines_filtered_out():
    """Verify that passed events ('no school Monday' 5d ago, 'tonight' yesterday, past weekend) are removed."""
    from datetime import datetime, timezone
    engine = DigestEngine()

    feed_items = [
        # Monday passed 2 days ago relative to Wednesday Sep 23
        {
            "id": "no_school",
            "author_name": "School Office",
            "content_text": "REMINDER: There is no school on Monday",
            "item_timestamp": "2026-09-18T12:00:00Z"  # Friday Sep 18 (5 days ago)
        },
        # Town hall meeting was Tuesday night
        {
            "id": "town_hall",
            "author_name": "Principal",
            "content_text": "Families, here is a friendly reminder that tonight is the town hall meeting",
            "item_timestamp": "2026-09-22T15:00:00Z"  # Tuesday 3:00 PM
        },
        # Weekend passed 3 days ago
        {
            "id": "weekend_login",
            "author_name": "School Office",
            "content_text": "Happy Weekend Seahawks! Parents, please take time this weekend to login to your portal",
            "item_timestamp": "2026-09-18T12:00:00Z"  # Friday Sep 18
        },
        # Daily homework assigned yesterday afternoon remains active
        {
            "id": "daily_hw",
            "author_name": "Ms. Miller",
            "content_text": "Daily Homework: Students need to complete the Monday and Tuesday pages in their ELA packet",
            "item_timestamp": "2026-09-22T19:54:00Z"  # Tuesday evening
        }
    ]

    wednesday_morning = datetime(2026, 9, 23, 7, 0, tzinfo=timezone.utc)
    briefing = engine.synthesize(feed_items=feed_items, messages=[], events=[], ref_dt=wednesday_morning)

    # Active items should ONLY contain the relevant homework!
    active_summaries = [a.summary for a in briefing.active_action_items]
    assert any("Daily Homework" in s for s in active_summaries)
    assert not any("no school on Monday" in s for s in active_summaries)
    assert not any("town hall meeting" in s for s in active_summaries)

    # Expired past notices should NOT clutter with transient noise
    expired_summaries = [a.summary for a in briefing.expired_action_items]
    assert not any("no school on Monday" in s for s in expired_summaries)
    assert not any("town hall meeting" in s for s in expired_summaries)

    # Upcoming dates should NOT contain the past weekend
    upcoming_titles = [d.title for d in briefing.active_upcoming_dates]
    assert not any("this weekend" in t.lower() for t in upcoming_titles)


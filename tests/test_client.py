"""Unit tests for the ClassDojo API client and cookie management."""

import json
import tempfile
from pathlib import Path
import httpx
import pytest
from dojo.client import DojoClient, TwoFactorRequiredError, LOGIN_URL


def test_session_save_and_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = Path(tmpdir) / "session.json"
        client = DojoClient(session_file=session_file)

        # Manually set a cookie
        client.client.cookies.set("dojo_login.sid", "test_cookie_value", domain="home.classdojo.com")
        client.save_session()

        assert session_file.exists()
        saved_data = json.loads(session_file.read_text())
        assert saved_data.get("dojo_login.sid") == "test_cookie_value"

        # Create a new client instance and check loading
        client2 = DojoClient(session_file=session_file)
        assert client2.client.cookies.get("dojo_login.sid", domain="home.classdojo.com") == "test_cookie_value"
        client.close()
        client2.close()


def test_login_otc_flow():
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = Path(tmpdir) / "session.json"

        # Mock transport simulating OTC challenge then success
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            data = json.loads(request.content)

            if "code" not in data:
                # First request: respond with OTC challenge
                return httpx.Response(
                    401,
                    json={
                        "error": {
                            "type": 401,
                            "code": "ERR_MUST_USE_OTC_USER_OPTED_IN",
                            "fallbackMessage": "Please enter the code sent to your email."
                        }
                    }
                )
            else:
                assert data["code"] == "123456"
                return httpx.Response(
                    200,
                    json={
                        "id": "user_123",
                        "name": "Parent User",
                        "email": data["login"]
                    }
                )

        client = DojoClient(session_file=session_file)
        client.client = httpx.Client(transport=httpx.MockTransport(handler))

        result = client.login(
            email="parent@example.com",
            password="secret_password",
            code_prompt=lambda: "123456"
        )

        assert call_count == 2
        assert result["id"] == "user_123"
        client.close()


def test_get_messages_fetches_class_updates_threads():
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = Path(tmpdir) / "session.json"

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/api/session"):
                return httpx.Response(
                    200,
                    json={
                        "parent": {"_id": "parent_999"},
                        "children": [{"_id": "child_1", "firstName": "Isabella"}],
                    },
                )
            if url.endswith("/api/parent/parent_999/message-thread"):
                return httpx.Response(
                    200,
                    json={
                        "_items": [
                            {
                                "_id": "thread_class_updates",
                                "name": "Ms. Alonso's Class",
                                "type": "b",
                            },
                            {
                                "_id": "thread_direct",
                                "name": "Ms. Alonso (Isabella's Teacher)",
                                "type": "a",
                            },
                        ]
                    },
                )
            if url.endswith("/api/message-thread/thread_class_updates/message"):
                return httpx.Response(
                    200,
                    json={
                        "_items": [
                            {
                                "_id": "msg_update_1",
                                "body": "Good evening, parents! There will be no tests this week due to our Halloween activities.",
                                "createdAt": "2026-10-28T00:07:15.000Z",
                                "sender": {
                                    "_id": "teacher_1",
                                    "formattedName": "Ms. Alonso",
                                },
                                "attachments": [],
                            }
                        ]
                    },
                )
            if url.endswith("/api/message-thread/thread_direct/message"):
                return httpx.Response(
                    200,
                    json={
                        "_items": [
                            {
                                "_id": "000000000000000000000000",
                                "body": "You joined the class",
                                "createdAt": "2026-08-22T20:20:05.870Z",
                            }
                        ]
                    },
                )
            return httpx.Response(404)

        client = DojoClient(session_file=session_file)
        client.client = httpx.Client(transport=httpx.MockTransport(handler))

        msgs = client.get_messages()
        assert len(msgs) == 1
        assert msgs[0]["id"] == "msg_update_1"
        assert "Class Updates" in msgs[0]["sender_name"]
        assert "no tests this week" in msgs[0]["body"]
        client.close()


def test_get_events_parses_calendar_event_endpoint():
    with tempfile.TemporaryDirectory() as tmpdir:
        session_file = Path(tmpdir) / "session.json"

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith("/api/calendar-event"):
                return httpx.Response(
                    200,
                    json={
                        "_items": [
                            {
                                "_id": "cal_123",
                                "contents": {
                                    "title": "Open House",
                                    "description": "Adult-only classroom orientation.",
                                },
                                "start": "2026-08-25T21:00:00.000Z",
                                "end": "2026-08-25T22:00:00.000Z",
                                "target": {
                                    "type": "class",
                                    "_id": "class_1",
                                    "name": "Ms. Alonso's Class",
                                },
                            }
                        ]
                    },
                )
            return httpx.Response(404)

        client = DojoClient(session_file=session_file)
        client.client = httpx.Client(transport=httpx.MockTransport(handler))

        events = client.get_events()
        assert len(events) == 1
        assert events[0]["id"] == "cal_123"
        assert events[0]["title"] == "Open House"
        assert events[0]["description"] == "Adult-only classroom orientation."
        assert events[0]["start_time"] == "2026-08-25T21:00:00.000Z"
        assert events[0]["location"] == "Ms. Alonso's Class"
        client.close()

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

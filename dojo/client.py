"""Direct REST API client for ClassDojo."""

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://home.classdojo.com"
LOGIN_URL = f"{BASE_URL}/api/session"
FEED_URL = f"{BASE_URL}/api/storyFeed?includePrivate=true"
MESSAGES_URL = f"{BASE_URL}/api/conversations"
EVENTS_URL = f"{BASE_URL}/api/events"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class TwoFactorRequiredError(Exception):
    """Raised when ClassDojo requires a One-Time Code (OTC) 2FA verification."""
    def __init__(self, message: str = "Two-factor verification code required."):
        super().__init__(message)


class DojoClient:
    """HTTP Client for communicating directly with ClassDojo internal REST APIs."""

    def __init__(self, session_file: Optional[Path] = None):
        self.session_file = Path(session_file) if session_file else None
        self.client = httpx.Client(
            headers=DEFAULT_HEADERS,
            timeout=30.0,
            follow_redirects=True,
        )
        if self.session_file and self.session_file.exists():
            self.load_session()

    def save_session(self) -> None:
        """Persist session cookies to local JSON file."""
        if not self.session_file:
            return
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_data = {}
        for cookie in self.client.cookies.jar:
            cookie_data[cookie.name] = cookie.value
        self.session_file.write_text(json.dumps(cookie_data, indent=2))
        logger.debug(f"Saved {len(cookie_data)} cookies to {self.session_file}")

    def load_session(self) -> bool:
        """Load session cookies from local JSON file."""
        if not self.session_file or not self.session_file.exists():
            return False
        try:
            cookie_dict = json.loads(self.session_file.read_text())
            for name, value in cookie_dict.items():
                self.client.cookies.set(name, value, domain="home.classdojo.com")
            logger.debug(f"Loaded {len(cookie_dict)} cookies from {self.session_file}")
            return True
        except Exception as e:
            logger.warning(f"Could not load session cookies: {e}")
            return False

    def is_authenticated(self) -> bool:
        """Check if current cookies provide a valid session."""
        try:
            resp = self.client.get(LOGIN_URL)
            if resp.status_code == 200:
                data = resp.json()
                # If logged in, session info has an id or user
                return bool(data.get("id") or data.get("user") or data.get("currentUserId"))
            return False
        except Exception:
            return False

    def login(
        self,
        email: str,
        password: str,
        code_prompt: Optional[Callable[[], str]] = None,
        max_attempts: int = 3
    ) -> Dict[str, Any]:
        """
        Log in with email and password, handling 2FA OTC verification if required.
        """
        payload: Dict[str, Any] = {
            "login": email,
            "password": password,
            "resumeAddClassFlow": False,
        }

        for attempt in range(max_attempts):
            response = self.client.post(LOGIN_URL, json=payload)
            if response.status_code in (200, 201):
                self.save_session()
                return response.json()

            if response.status_code == 401:
                try:
                    err_data = response.json().get("error", {})
                    err_code = err_data.get("code")
                except Exception:
                    err_code = None

                # Check if ClassDojo requires a one-time verification code
                if err_code in ("ERR_MUST_USE_OTC_USER_OPTED_IN", "ERR_MUST_USE_OTC_ANOMALOUS_LOGIN"):
                    if not code_prompt:
                        raise TwoFactorRequiredError(
                            "ClassDojo sent a one-time verification code to your email. Code prompt required."
                        )
                    code = code_prompt().strip()
                    payload["code"] = code
                    continue

                error_msg = response.text
                try:
                    error_msg = response.json().get("error", {}).get("message", error_msg)
                except Exception:
                    pass
                raise ValueError(f"Login failed (401 Unauthorized): {error_msg}")

            response.raise_for_status()

        raise RuntimeError("Exceeded maximum login attempts.")

    def get_session_info(self) -> Dict[str, Any]:
        """Fetch current user profile, children, and enrolled classrooms."""
        resp = self.client.get(LOGIN_URL)
        resp.raise_for_status()
        return resp.json()

    def get_story_feed(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Fetch story feed items (announcements, posts, photos).
        Iterates backwards through pages if necessary up to `limit` items.
        """
        all_items: List[Dict[str, Any]] = []
        next_url: Optional[str] = FEED_URL

        while next_url and len(all_items) < limit:
            resp = self.client.get(next_url)
            if resp.status_code != 200:
                logger.warning(f"Feed request failed ({resp.status_code}): {resp.text[:200]}")
                break

            data = resp.json()
            items = data.get("_items", [])
            if not items:
                break

            all_items.extend(items)

            # Pagination handling
            links = data.get("_links", {})
            prev_link = links.get("prev", {}).get("href")
            if prev_link and prev_link != next_url:
                next_url = prev_link if prev_link.startswith("http") else f"{BASE_URL}{prev_link}"
            else:
                next_url = None

        return all_items[:limit]

    def get_messages(self) -> List[Dict[str, Any]]:
        """Fetch direct messages / conversations between parent and teacher."""
        try:
            resp = self.client.get(MESSAGES_URL)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                return data.get("_items") or data.get("conversations") or []
        except Exception as e:
            logger.debug(f"Could not fetch direct conversations: {e}")
        return []

    def get_events(self) -> List[Dict[str, Any]]:
        """Fetch upcoming calendar events."""
        try:
            resp = self.client.get(EVENTS_URL)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                return data.get("_items") or data.get("events") or []
        except Exception as e:
            logger.debug(f"Could not fetch events endpoint: {e}")
        return []

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

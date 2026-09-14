"""Interactive browser login fallback using Playwright.

Used when ClassDojo enforces bot mitigation or a manual CAPTCHA during login.
Opens a browser window, allows the user to log in naturally, and captures the
resulting session cookies into data/session.json.
"""

import json
from pathlib import Path
from typing import Optional


def interactive_browser_login(
    session_file: Path,
    login_url: str = "https://home.classdojo.com/"
) -> bool:
    """
    Launch Chromium in visible (headful) mode, wait for the user to complete login,
    and save session cookies to `session_file`.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright is not installed. To enable browser login fallback, run:\n"
            "  uv add playwright && uv run playwright install chromium"
        )

    session_file = Path(session_file)
    session_file.parent.mkdir(parents=True, exist_ok=True)

    print("\n🌐 Launching browser for ClassDojo login...")
    print("👉 Please log in to your ClassDojo account in the browser window.")
    print("👉 The window will automatically detect when login succeeds and save your session.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        page.goto(login_url)

        # Wait for user to log in - URL changes or dashboard indicator appears
        try:
            # When logged in, URL usually changes to something containing 'story' or child/parent dashboard
            page.wait_for_url(
                lambda url: "home.classdojo.com" in url and "login" not in url.lower(),
                timeout=180000  # 3 minutes for user to complete
            )
            # Give a brief moment for cookies to settle
            page.wait_for_timeout(2000)

            cookies = context.cookies()
            cookie_dict = {c["name"]: c["value"] for c in cookies if "classdojo.com" in c["domain"]}

            if not cookie_dict:
                print("❌ No ClassDojo cookies found after login.")
                browser.close()
                return False

            session_file.write_text(json.dumps(cookie_dict, indent=2))
            print(f"✅ Successfully captured {len(cookie_dict)} session cookies to {session_file}!")
            browser.close()
            return True

        except Exception as e:
            print(f"❌ Login timed out or error occurred: {e}")
            browser.close()
            return False

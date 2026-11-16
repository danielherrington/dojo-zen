"""Configuration management for Dojo Agent."""

import os
from pathlib import Path
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load from local project .env if present (prevent scanning parent directories)
_project_root = Path(__file__).resolve().parent.parent.parent
_env_file = _project_root / ".env"
if _env_file.exists():
    load_dotenv(dotenv_path=_env_file)


def _positive_int(name: str, default: int) -> int:
    """Read a positive integer env var, falling back to the default if it's unset or invalid."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


class Settings(BaseModel):
    # ClassDojo
    dojo_email: str = Field(default_factory=lambda: os.getenv("DOJO_EMAIL", ""))
    dojo_password: str = Field(default_factory=lambda: os.getenv("DOJO_PASSWORD", ""))
    dojo_session_file: Path = Field(
        default_factory=lambda: Path(os.getenv("DOJO_SESSION_FILE", "data/session.json"))
    )
    dojo_db_path: Path = Field(
        default_factory=lambda: Path(os.getenv("DOJO_DB_PATH", "data/dojo.db"))
    )

    # Email SMTP
    smtp_host: str = Field(default_factory=lambda: os.getenv("SMTP_HOST", "smtp.gmail.com"))
    smtp_port: int = Field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    smtp_user: str = Field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    smtp_pass: str = Field(default_factory=lambda: os.getenv("SMTP_PASS", ""))
    smtp_use_tls: bool = Field(
        default_factory=lambda: os.getenv("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes")
    )
    email_from: str = Field(
        default_factory=lambda: os.getenv("EMAIL_FROM", "DojoZen Daily Briefing <dojo@example.com>")
    )
    email_to: str = Field(default_factory=lambda: os.getenv("EMAIL_TO", ""))

    # Optional AI Summarizer
    gemini_api_key: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))

    # Cloud / Firestore
    use_firestore: bool = Field(
        default_factory=lambda: os.getenv("USE_FIRESTORE", "").lower() in ("true", "1", "yes")
        or bool(os.getenv("K_SERVICE"))
    )
    google_cloud_project: str = Field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT", os.getenv("GCP_PROJECT", "herrington-ai-site"))
    )
    # Twilio / SMS & WhatsApp Assistant
    twilio_account_sid: str = Field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID", ""))
    twilio_auth_token: str = Field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN", ""))
    family_phone_numbers: str = Field(
        default_factory=lambda: os.getenv("FAMILY_PHONE_NUMBERS", "")
    )  # Comma-separated E.164 phone numbers (e.g. "+13015550123,+13015550124")

    # Web Portal URL for Email Links
    app_base_url: str = Field(
        default_factory=lambda: os.getenv("APP_BASE_URL", "https://dojo.herrington.ai")
    )

    # IANA timezone for the family's wall clock: Today/Tomorrow bucketing, Morning/Evening label,
    # weekend send window (DAN-165). Cloud Run itself runs in UTC.
    digest_timezone: str = Field(
        default_factory=lambda: os.getenv("DIGEST_TIMEZONE", "America/New_York")
    )

    # No hard-coded default: an unset CRON_SECRET rejects every cron call on Cloud Run (DAN-166).
    cron_secret: str = Field(default_factory=lambda: os.getenv("CRON_SECRET", ""))

    # Access control (DAN-166). Inert until REQUIRE_AUTH=true so deploys never lock out clients.
    dojo_access_token: str = Field(
        default_factory=lambda: os.getenv("DOJO_ACCESS_TOKEN", "")
    )  # Comma-separated; any one grants access to the portal, /api/* and /mcp
    require_auth: bool = Field(
        default_factory=lambda: os.getenv("REQUIRE_AUTH", "").lower() in ("true", "1", "yes")
    )

    # Analytics (DAN-200): email open/click tracking and per-channel daily usage counters.
    email_tracking: bool = Field(
        default_factory=lambda: os.getenv("EMAIL_TRACKING", "true").lower() in ("true", "1", "yes")
    )
    usage_analytics: bool = Field(
        default_factory=lambda: os.getenv("USAGE_ANALYTICS", "true").lower() in ("true", "1", "yes")
    )

    # Retention (DAN-205). Per-event tracking rows (email sends, opens, clicks) are deleted after
    # ANALYTICS_RETENTION_DAYS; the aggregate daily usage counters after USAGE_RETENTION_DAYS.
    analytics_retention_days: int = Field(
        default_factory=lambda: _positive_int("ANALYTICS_RETENTION_DAYS", 90)
    )
    usage_retention_days: int = Field(
        default_factory=lambda: _positive_int("USAGE_RETENTION_DAYS", 400)
    )

    def ensure_directories(self) -> None:
        """Ensure parent directories for session and database files exist."""
        self.dojo_session_file.parent.mkdir(parents=True, exist_ok=True)
        self.dojo_db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()

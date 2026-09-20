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

    cron_secret: str = Field(default_factory=lambda: os.getenv("CRON_SECRET", "dojo-zen-cron-token"))

    def ensure_directories(self) -> None:
        """Ensure parent directories for session and database files exist."""
        self.dojo_session_file.parent.mkdir(parents=True, exist_ok=True)
        self.dojo_db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()

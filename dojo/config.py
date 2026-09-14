"""Configuration management for Dojo Agent."""

import os
from pathlib import Path
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load from local project .env if present (prevent scanning parent directories)
_project_root = Path(__file__).resolve().parent.parent
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
        default_factory=lambda: os.getenv("EMAIL_FROM", "ClassDojo Digest <dojo@example.com>")
    )
    email_to: str = Field(default_factory=lambda: os.getenv("EMAIL_TO", ""))

    # Optional AI Summarizer
    gemini_api_key: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))

    def ensure_directories(self) -> None:
        """Ensure parent directories for session and database files exist."""
        self.dojo_session_file.parent.mkdir(parents=True, exist_ok=True)
        self.dojo_db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()

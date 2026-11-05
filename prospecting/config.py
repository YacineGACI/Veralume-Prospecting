"""Settings: the prospecting area, paths and politeness limits.

Override any field with a PROSPECT_* environment variable or in `.env`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PROSPECT_", env_file=ROOT / ".env", extra="ignore"
    )

    # Area: a circle around Strasbourg (Eurométropole and the nearby towns).
    center_name: str = "Strasbourg"
    center_lat: float = 48.5734
    center_lon: float = 7.7521
    radius_km: float = 30.0

    data_dir: Path = ROOT / "data"
    # The database: PostgreSQL, e.g. postgresql+psycopg://user:pw@127.0.0.1:5433/prospecting.
    # Empty: a SQLite file in data_dir (the tests use that).
    database_url: str | None = None
    # Screening runs Veralume's own line measurement, in Veralume's environment.
    veralume_dir: Path = ROOT.parent / "Veralume"

    # Politeness. We identify ourselves, follow robots.txt and wait between requests.
    user_agent: str = "VeralumeProspecting/0.1 (B2B lead research)"
    host_interval_s: float = 1.5
    search_interval_s: float = 25.0
    cache_days: int = 14

    max_pages_per_site: int = 8
    photos_per_lead: int = 4

    # Optional. Google Places (New) Text Search; free monthly quota, cap it in the console.
    google_places_key: str | None = None

    # Your own mailbox. Emails go out over SMTP from your address; replies are read over
    # IMAP (read-only, nothing is moved or marked). Gmail: an app password, smtp.gmail.com
    # port 465, imap.gmail.com.
    smtp_host: str | None = None
    smtp_port: int = 465  # 465 = SSL, 587 = STARTTLS
    smtp_user: str | None = None
    smtp_password: str | None = None
    imap_host: str | None = None
    imap_port: int = 993
    mail_from: str | None = None  # defaults to smtp_user
    mail_from_name: str = "Veralume"
    # Write emails to data/outbox/*.eml instead of sending them (for testing).
    mail_dry_run: bool = False
    # Protect the mailbox: a daily cap, a sending window, a random gap between emails.
    daily_send_cap: int = 30
    send_hours: tuple[int, int] = (9, 18)
    send_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)  # Monday = 0
    send_gap_s: tuple[int, int] = (120, 360)
    inbox_poll_s: int = 300
    timezone: str = "Europe/Paris"
    # Who the emails say you are, for the legal footer.
    sender_city: str = "Strasbourg"
    # Run the background sender and inbox reader inside `prospect serve`.
    run_worker: bool = True

    # Writing emails from a prompt. Set any of these; the one used is chosen in Settings.
    # Free: a Gemini or Mistral free-tier key, or Ollama running on this machine.
    # Paid: an Anthropic API key (Claude).
    gemini_api_key: str | None = None
    mistral_api_key: str | None = None
    anthropic_api_key: str | None = None  # or ANTHROPIC_API_KEY
    ollama_url: str | None = None  # e.g. http://localhost:11434
    ai_model_gemini: str = "gemini-2.5-flash"
    ai_model_mistral: str = "mistral-small-latest"
    ai_model_claude: str = "claude-opus-5-5"
    ai_model_ollama: str = "qwen2.5:3b"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "prospecting.db"

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.db_path}"

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"

    @property
    def mail_configured(self) -> bool:
        return bool(self.mail_dry_run or (self.smtp_host and self.smtp_user and self.smtp_password))

    @property
    def sender(self) -> str | None:
        return self.mail_from or self.smtp_user or ("you@example.org" if self.mail_dry_run else None)

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def veralume_python(self) -> Path:
        return self.veralume_dir / ".venv" / "bin" / "python"


settings = Settings()

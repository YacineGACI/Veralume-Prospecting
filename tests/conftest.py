"""Tests run on the PostgreSQL test database and a dry-run mailbox: nothing is ever sent,
and the real database is never touched. Set before any prospecting import."""

import os
import tempfile
from pathlib import Path

from sqlalchemy.engine import make_url


def _test_database_url() -> str:
    """PROSPECT_TEST_DATABASE_URL, else the app's database from .env with "_test" added to
    its name. Refuses any database whose name does not end in _test."""
    url = os.environ.get("PROSPECT_TEST_DATABASE_URL", "")
    if not url:
        env = Path(__file__).resolve().parents[1] / ".env"
        lines = env.read_text().splitlines() if env.exists() else []
        app = next((l.partition("=")[2].strip().strip("'\"") for l in lines if l.startswith("PROSPECT_DATABASE_URL=")), "")
        if app:
            u = make_url(app)
            url = u.set(database=f"{u.database}_test").render_as_string(hide_password=False)
    if not url.startswith("postgresql") or not (make_url(url).database or "").endswith("_test"):
        raise SystemExit("Tests need a PostgreSQL test database: set PROSPECT_TEST_DATABASE_URL, or "
                         "PROSPECT_DATABASE_URL in .env (its <name>_test database is used).")
    return url

_TMP = tempfile.mkdtemp(prefix="prospect-test-")
os.environ["PROSPECT_DATA_DIR"] = _TMP
os.environ["PROSPECT_MAIL_DRY_RUN"] = "true"
os.environ["PROSPECT_RUN_WORKER"] = "false"
os.environ["PROSPECT_MAIL_FROM"] = "moi@veralume.test"
# Never the real database from .env: its _test twin.
os.environ["PROSPECT_DATABASE_URL"] = _test_database_url()
for k in ("PROSPECT_SMTP_HOST", "PROSPECT_SMTP_USER", "PROSPECT_SMTP_PASSWORD", "PROSPECT_IMAP_HOST",
          "PROSPECT_GEMINI_API_KEY", "PROSPECT_MISTRAL_API_KEY", "PROSPECT_ANTHROPIC_API_KEY",
          "PROSPECT_OLLAMA_URL", "ANTHROPIC_API_KEY"):
    os.environ.pop(k, None)

import pytest  # noqa: E402


@pytest.fixture
def db():
    """An empty database with the seed templates."""
    from prospecting import contacts, mailer
    from prospecting.db import Base, engine, session

    with engine.begin() as c:
        for t in reversed(Base.metadata.sorted_tables):
            c.execute(t.delete())
    contacts._CACHE["at"] = -1e9
    mailer._NEXT_SLOT.clear()
    with session() as s:
        mailer.ensure_templates(s)
    yield session

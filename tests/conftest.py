"""Tests run on a throwaway database and a dry-run mailbox: nothing is ever sent, and the
real data/prospecting.db is never touched. Set before any prospecting import."""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="prospect-test-")
os.environ["PROSPECT_DATA_DIR"] = _TMP
os.environ["PROSPECT_MAIL_DRY_RUN"] = "true"
os.environ["PROSPECT_RUN_WORKER"] = "false"
os.environ["PROSPECT_MAIL_FROM"] = "moi@veralume.test"
for k in ("PROSPECT_SMTP_HOST", "PROSPECT_SMTP_USER", "PROSPECT_SMTP_PASSWORD", "PROSPECT_IMAP_HOST"):
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

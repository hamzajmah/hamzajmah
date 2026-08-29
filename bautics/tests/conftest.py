"""Gemeinsame Test-Vorbereitung.

Wichtig: Die Umgebungsvariablen muessen stehen, BEVOR ``app.config`` importiert
wird - die Konfiguration liest sie beim Import. Kein Test spricht mit einem
echten Dienst; alle Aussenkontakte sind Attrappen.
"""

import os
import tempfile

_TESTABLAGE = tempfile.mkdtemp(prefix="bautics-tests-")

os.environ.setdefault("BAUTICS_STORAGE_DIR", os.path.join(_TESTABLAGE, "storage"))
os.environ.setdefault("BAUTICS_KNOWLEDGE_DIR", os.path.join(_TESTABLAGE, "wissensbank"))
os.environ.setdefault("BAUTICS_DATABASE_URL", f"sqlite:///{_TESTABLAGE}/bautics.db")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("DEEPGRAM_API_KEY", "test-deepgram-key")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "AC-test")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test-twilio-token")
os.environ.setdefault("BAUTICS_BASE_URL", "https://echo.bautics.test")

import pytest  # noqa: E402

from app import db  # noqa: E402


@pytest.fixture
def datenbank(tmp_path):
    """Frische SQLite-Datenbank je Test."""
    engine = db.erzeuge_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.setze_engine(engine)
    db.init_db()
    yield engine
    engine.dispose()

"""Zentrale Konfiguration - alles kommt aus Umgebungsvariablen (.env)."""

import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# Modelle: Was der Kunde liest -> Opus. Was die Pipeline sortiert -> Haiku.
MODEL_REPORT = _env("BAUTICS_MODEL_REPORT", "claude-opus-5")
MODEL_FAST = _env("BAUTICS_MODEL_FAST", "claude-haiku-4-5")

STT_PROVIDER = _env("BAUTICS_STT_PROVIDER", "openai")
OPENAI_API_KEY = _env("OPENAI_API_KEY")
DEEPGRAM_API_KEY = _env("DEEPGRAM_API_KEY")

TWILIO_ACCOUNT_SID = _env("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _env("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = _env("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

BASE_URL = _env("BAUTICS_BASE_URL", "http://localhost:8000").rstrip("/")
DATABASE_URL = _env("BAUTICS_DATABASE_URL", "sqlite:///./data/bautics.db")

STORAGE_DIR = Path(_env("BAUTICS_STORAGE_DIR", "./data/storage"))
KNOWLEDGE_DIR = Path(_env("BAUTICS_KNOWLEDGE_DIR", "./data/wissensbank"))

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)

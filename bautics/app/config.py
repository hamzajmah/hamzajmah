"""Zentrale Konfiguration - alles kommt aus Umgebungsvariablen (.env).

Modell- und Providerwahl stehen ausschliesslich hier, niemals im Feature-Code.
Nach aussen (Berichte, PDFs, Website) heisst das Ganze immer nur
"Bautics Engine" - Modellnamen bleiben in dieser Datei.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        logger.warning("Ungueltiger Zahlenwert in %s, nutze Standard %s", name, default)
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        logger.warning("Ungueltiger Zahlenwert in %s, nutze Standard %s", name, default)
        return default


# --- Modellzugriff: ausschliesslich ueber OpenRouter (OpenAI-kompatible API) ---

OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = _env(
    "BAUTICS_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
).rstrip("/")

# Modellwahl je Use Case - zentral, damit sie nicht im Feature-Code verstreut ist.
# Echo: Sprachmemo -> strukturierter Tagesbericht.
MODEL_ECHO = _env("BAUTICS_MODEL_ECHO", "anthropic/claude-sonnet-5")
# Mind: Frage an die Wissensbank -> Antwort mit Fundstelle.
MODEL_MIND = _env("BAUTICS_MODEL_MIND", "anthropic/claude-sonnet-5")
# Mind: Vektoren fuer die Bedeutungssuche ueber die Projektdokumente.
MODEL_EMBEDDING = _env("BAUTICS_MODEL_EMBEDDING", "qwen/qwen3-embedding-8b")

# Provider-Routing fuer jeden Modellaufruf:
# - zdr: nur Provider mit Zero Data Retention
# - data_collection "deny": keine Weiterverwendung der Kundendaten
# - require_parameters: nur Provider, die unsere Parameter (Structured Outputs)
#   auch wirklich umsetzen - sonst lieber ein Fehler als ein stiller Fallback.
OPENROUTER_PROVIDER_ROUTING: dict[str, object] = {
    "zdr": True,
    "data_collection": "deny",
    "require_parameters": True,
}

# Strukturierung ist Extraktion, keine Kreativitaet.
LLM_TEMPERATUR = _env_float("BAUTICS_LLM_TEMPERATUR", 0.0)
LLM_MAX_TOKENS = _env_int("BAUTICS_LLM_MAX_TOKENS", 4000)

# Attribution gegenueber OpenRouter (taucht nur in deren Dashboard auf).
OPENROUTER_APP_NAME = "Bautics"

# --- Spracherkennung (kein OpenRouter-Aufruf, eigener Provider) ---

STT_PROVIDER = _env("BAUTICS_STT_PROVIDER", "openai").lower()
OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_BASE_URL = _env("BAUTICS_OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
STT_MODEL_OPENAI = _env("BAUTICS_STT_MODEL_OPENAI", "whisper-1")

DEEPGRAM_API_KEY = _env("DEEPGRAM_API_KEY")
DEEPGRAM_BASE_URL = _env("BAUTICS_DEEPGRAM_BASE_URL", "https://api.deepgram.com/v1").rstrip("/")
STT_MODEL_DEEPGRAM = _env("BAUTICS_STT_MODEL_DEEPGRAM", "nova-2")

STT_SPRACHE = _env("BAUTICS_STT_SPRACHE", "de")

# Whisper nimmt maximal 25 MB entgegen - groessere Anhaenge lehnen wir frueh ab.
MAX_AUDIO_BYTES = _env_int("BAUTICS_MAX_AUDIO_BYTES", 25 * 1024 * 1024)

# --- HTTP: Timeouts und Retries mit exponentiellem Backoff ---

HTTP_TIMEOUT_SEKUNDEN = _env_float("BAUTICS_HTTP_TIMEOUT", 120.0)
HTTP_MAX_VERSUCHE = _env_int("BAUTICS_HTTP_MAX_VERSUCHE", 3)
HTTP_BACKOFF_BASIS_SEKUNDEN = _env_float("BAUTICS_HTTP_BACKOFF_BASIS", 1.0)

# --- Twilio (WhatsApp) ---

TWILIO_ACCOUNT_SID = _env("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = _env("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = _env("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
TWILIO_API_BASE_URL = _env("BAUTICS_TWILIO_BASE_URL", "https://api.twilio.com").rstrip("/")
# Signaturpruefung nur fuer lokale Tests abschaltbar - in Produktion immer an.
TWILIO_SIGNATUR_PRUEFEN = _env("BAUTICS_TWILIO_SIGNATUR_PRUEFEN", "true").lower() != "false"

BASE_URL = _env("BAUTICS_BASE_URL", "http://localhost:8000").rstrip("/")
DATABASE_URL = _env("BAUTICS_DATABASE_URL", "sqlite:///./data/bautics.db")

STORAGE_DIR = Path(_env("BAUTICS_STORAGE_DIR", "./data/storage"))
KNOWLEDGE_DIR = Path(_env("BAUTICS_KNOWLEDGE_DIR", "./data/wissensbank"))

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)


# --- Mind: Wissensbank ueber die Projektdokumente ---

# Zerlegung der Dokumente. Geschnitten wird an natuerlichen Grenzen
# (Absatz/Abschnitt); die Zeichenzahl ist die Obergrenze, nicht das Raster.
MIND_CHUNK_MAX_ZEICHEN = _env_int("BAUTICS_MIND_CHUNK_MAX", 1400)
MIND_CHUNK_MIN_ZEICHEN = _env_int("BAUTICS_MIND_CHUNK_MIN", 80)
MIND_CHUNK_UEBERLAPPUNG = _env_int("BAUTICS_MIND_CHUNK_UEBERLAPPUNG", 200)

# Embeddings: Groesse eines Aufrufs und Dimension des Vektors.
# Die Dimension bestimmt auf Postgres die DDL der pgvector-Spalte und muss
# zum gewaehlten Embedding-Modell passen - sonst nimmt die Datenbank die
# Vektoren nicht an.
EMBEDDING_BATCH = _env_int("BAUTICS_EMBEDDING_BATCH", 32)
EMBEDDING_DIMENSIONEN = _env_int("BAUTICS_EMBEDDING_DIMENSIONEN", 4096)

# Suche: wie viele Kandidaten je Verfahren, wie viele Belege ans Modell.
MIND_KANDIDATEN = _env_int("BAUTICS_MIND_KANDIDATEN", 20)
MIND_BELEGE = _env_int("BAUTICS_MIND_BELEGE", 8)
# Konstante der Reciprocal Rank Fusion - Standardwert aus der Literatur.
MIND_RRF_K = _env_int("BAUTICS_MIND_RRF_K", 60)
# Untergrenze der Kosinus-Aehnlichkeit. Liegt kein Treffer darueber und gibt es
# auch keinen exakten Textfund, fragen wir das Modell gar nicht erst.
MIND_MIN_AEHNLICHKEIT = _env_float("BAUTICS_MIND_MIN_AEHNLICHKEIT", 0.30)
# Ein Belegzitat muss lang genug sein, um wirklich etwas zu belegen.
MIND_MIN_ZITAT_ZEICHEN = _env_int("BAUTICS_MIND_MIN_ZITAT", 20)

# --- Zugriffsschutz der Mind-Schnittstelle ---

# Wenn gesetzt, verlangen die /mind-Routen "Authorization: Bearer <Token>".
# Ohne Token sind die Routen offen - das ist nur fuer lokale Entwicklung
# vertretbar; in Produktion ist der Wert Pflicht (Kundendokumente!).
API_TOKEN = _env("BAUTICS_API_TOKEN")


# --- Zuordnung Absender -> Baulos ---

PROJEKT_STANDARD = _env("BAUTICS_PROJEKT_STANDARD", "Unbekanntes Baulos")


def _projekt_zuordnung() -> dict[str, str]:
    """JSON-Map 'whatsapp:+49...' -> Baulos, z.B. {"whatsapp:+4915112345678": "Los 4"}."""
    rohwert = _env("BAUTICS_PROJEKT_ZUORDNUNG")
    if not rohwert:
        return {}
    try:
        geparst = json.loads(rohwert)
    except json.JSONDecodeError:
        logger.warning("BAUTICS_PROJEKT_ZUORDNUNG ist kein gueltiges JSON - ignoriert.")
        return {}
    if not isinstance(geparst, dict):
        logger.warning("BAUTICS_PROJEKT_ZUORDNUNG muss ein JSON-Objekt sein - ignoriert.")
        return {}
    return {str(k): str(v) for k, v in geparst.items()}


PROJEKT_ZUORDNUNG = _projekt_zuordnung()


def projekt_fuer_absender(absender: str) -> str:
    """Baulos zur Telefonnummer, sonst der konfigurierte Standard."""
    return PROJEKT_ZUORDNUNG.get(absender, PROJEKT_STANDARD)

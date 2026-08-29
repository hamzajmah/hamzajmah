"""Echo, Schritt 1: Audio -> Transkript.

Die Spracherkennung laeuft nicht ueber OpenRouter, sondern direkt beim
gewaehlten Provider (Whisper oder Deepgram). Beiden geben wir den
Baustellen-Kontext aus ``glossary.py`` mit, damit Fachbegriffe und
Stationierungen korrekt ankommen ("zwoelf-vier" -> 12+400).
"""

import logging

import httpx

from . import config
from .glossary import GLOSSAR_BEGRIFFE, STT_KONTEXT
from .http_client import UpstreamFehler, anfrage_mit_retry, json_antwort

logger = logging.getLogger(__name__)

# Dateiendung je MIME-Typ - Whisper erkennt das Format an der Endung.
_ENDUNGEN = {
    "audio/ogg": "ogg",
    "application/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/aac": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/amr": "amr",
    "audio/3gpp": "3gp",
}


class TranskriptionsFehler(RuntimeError):
    """Die Sprachnachricht konnte nicht in Text ueberfuehrt werden."""


def _dateiname(content_type: str) -> str:
    basis = (content_type or "").split(";")[0].strip().lower()
    return f"sprachnachricht.{_ENDUNGEN.get(basis, 'ogg')}"


def transkribiere(
    audio: bytes,
    content_type: str = "audio/ogg",
    *,
    client: httpx.Client | None = None,
) -> str:
    """Audio in deutschen Text uebersetzen, mit Bau-Glossar als Kontext."""
    if not audio:
        raise TranskriptionsFehler("Leere Audiodatei erhalten.")
    if len(audio) > config.MAX_AUDIO_BYTES:
        raise TranskriptionsFehler(
            f"Audiodatei zu gross ({len(audio)} Bytes, erlaubt sind "
            f"{config.MAX_AUDIO_BYTES})."
        )

    eigener_client = client is None
    aktiver_client = client or httpx.Client(timeout=config.HTTP_TIMEOUT_SEKUNDEN)
    try:
        if config.STT_PROVIDER == "deepgram":
            text = _deepgram(aktiver_client, audio, content_type)
        elif config.STT_PROVIDER == "openai":
            text = _whisper(aktiver_client, audio, content_type)
        else:
            raise TranskriptionsFehler(
                f"Unbekannter STT-Provider konfiguriert: {config.STT_PROVIDER!r}"
            )
    except UpstreamFehler as fehler:
        raise TranskriptionsFehler(str(fehler)) from fehler
    finally:
        if eigener_client:
            aktiver_client.close()

    text = text.strip()
    if not text:
        raise TranskriptionsFehler("Spracherkennung lieferte keinen Text.")
    # Nur Metadaten loggen - der Inhalt ist Kundendatum.
    logger.info("Transkript erstellt (%s Zeichen, Provider %s).", len(text), config.STT_PROVIDER)
    return text


def _whisper(client: httpx.Client, audio: bytes, content_type: str) -> str:
    if not config.OPENAI_API_KEY:
        raise TranskriptionsFehler("OPENAI_API_KEY ist nicht gesetzt.")

    def aufruf() -> httpx.Response:
        return client.post(
            f"{config.OPENAI_BASE_URL}/audio/transcriptions",
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
            files={"file": (_dateiname(content_type), audio, content_type or "audio/ogg")},
            data={
                "model": config.STT_MODEL_OPENAI,
                "language": config.STT_SPRACHE,
                # Kontext-Prompt mit Fachbegriffen und Stationierungen
                "prompt": STT_KONTEXT,
                "response_format": "json",
            },
        )

    daten = json_antwort("Spracherkennung", anfrage_mit_retry("Spracherkennung", aufruf))
    text = daten.get("text")
    if not isinstance(text, str):
        raise TranskriptionsFehler("Antwort der Spracherkennung enthielt keinen Text.")
    return text


def _deepgram(client: httpx.Client, audio: bytes, content_type: str) -> str:
    if not config.DEEPGRAM_API_KEY:
        raise TranskriptionsFehler("DEEPGRAM_API_KEY ist nicht gesetzt.")

    # Deepgram kennt keinen Prompt, sondern gewichtete Schluesselbegriffe -
    # inhaltlich dieselbe Rolle wie STT_KONTEXT bei Whisper.
    parameter = [
        ("model", config.STT_MODEL_DEEPGRAM),
        ("language", config.STT_SPRACHE),
        ("smart_format", "true"),
        ("punctuate", "true"),
        *[("keywords", f"{begriff}:2") for begriff in GLOSSAR_BEGRIFFE],
    ]

    def aufruf() -> httpx.Response:
        return client.post(
            f"{config.DEEPGRAM_BASE_URL}/listen",
            headers={
                "Authorization": f"Token {config.DEEPGRAM_API_KEY}",
                "Content-Type": content_type or "audio/ogg",
            },
            params=parameter,
            content=audio,
        )

    daten = json_antwort("Spracherkennung", anfrage_mit_retry("Spracherkennung", aufruf))
    try:
        return daten["results"]["channels"][0]["alternatives"][0]["transcript"]
    except (KeyError, IndexError, TypeError) as fehler:
        raise TranskriptionsFehler(
            "Antwort der Spracherkennung hatte ein unerwartetes Format."
        ) from fehler

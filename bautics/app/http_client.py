"""Gemeinsamer HTTP-Zugriff: Timeouts, Retries mit exponentiellem Backoff.

Alle ausgehenden Aufrufe (OpenRouter, Spracherkennung, Twilio) laufen hierueber,
damit Fehlerpfade an einer Stelle definiert sind. Es werden bewusst nur
Statuscodes und Versuchszaehler geloggt - niemals Inhalte oder Kundendaten.
"""

import logging
import time
from typing import Any, Callable

import httpx

from . import config

logger = logging.getLogger(__name__)

# Nur diese Statuscodes sind es wert, erneut versucht zu werden.
WIEDERHOLBARE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class UpstreamFehler(RuntimeError):
    """Ein externer Dienst hat dauerhaft nicht geantwortet oder abgelehnt."""

    def __init__(self, dienst: str, hinweis: str, status: int | None = None) -> None:
        super().__init__(f"{dienst}: {hinweis}")
        self.dienst = dienst
        self.status = status


def _wartezeit(versuch: int) -> float:
    """Exponentieller Backoff: 1s, 2s, 4s ... (Basis konfigurierbar)."""
    return config.HTTP_BACKOFF_BASIS_SEKUNDEN * (2 ** (versuch - 1))


def anfrage_mit_retry(
    dienst: str,
    aufruf: Callable[[], httpx.Response],
    *,
    max_versuche: int | None = None,
    schlafen: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """Fuehrt ``aufruf`` aus und wiederholt bei Netz-/Serverfehlern.

    ``aufruf`` muss die komplette Anfrage kapseln (inkl. frischem Request-Body,
    da Streams nach einem Fehlversuch nicht erneut gelesen werden koennen).
    """
    versuche = max_versuche or config.HTTP_MAX_VERSUCHE
    letzter_status: int | None = None
    letzter_hinweis = "keine Antwort erhalten"

    for versuch in range(1, versuche + 1):
        try:
            antwort = aufruf()
        except httpx.HTTPError as fehler:
            letzter_hinweis = f"Netzwerkfehler ({type(fehler).__name__})"
            logger.warning(
                "%s: Versuch %s/%s fehlgeschlagen (%s)",
                dienst,
                versuch,
                versuche,
                type(fehler).__name__,
            )
        else:
            if antwort.status_code < 400:
                return antwort
            letzter_status = antwort.status_code
            letzter_hinweis = f"HTTP {antwort.status_code}"
            if antwort.status_code not in WIEDERHOLBARE_STATUS:
                # Client-Fehler (z.B. 401, 422) wiederholen sich sowieso.
                raise UpstreamFehler(dienst, letzter_hinweis, antwort.status_code)
            logger.warning(
                "%s: Versuch %s/%s mit HTTP %s",
                dienst,
                versuch,
                versuche,
                antwort.status_code,
            )

        if versuch < versuche:
            schlafen(_wartezeit(versuch))

    raise UpstreamFehler(dienst, letzter_hinweis, letzter_status)


def json_antwort(dienst: str, antwort: httpx.Response) -> dict[str, Any]:
    """Antwort als JSON-Objekt, mit klarer Fehlermeldung statt Stacktrace."""
    try:
        daten = antwort.json()
    except ValueError as fehler:
        raise UpstreamFehler(dienst, "Antwort war kein gueltiges JSON") from fehler
    if not isinstance(daten, dict):
        raise UpstreamFehler(dienst, "Antwort war kein JSON-Objekt")
    return daten

"""FastAPI-Einstiegspunkt.

Der HTTP-Layer macht hier ausschliesslich drei Dinge: Signatur pruefen,
Formularfelder in ein Fachobjekt uebersetzen und die Antwort formulieren.
Die eigentliche Logik steht in ``echo.py`` und ist ohne HTTP testbar.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator
from xml.sax.saxutils import escape

from fastapi import BackgroundTasks, FastAPI, Request, Response, status

from . import config, db
from .echo import EingehendeNachricht, maskiere_nummer, nimm_nachricht_an, verarbeite_bericht
from .twilio_api import signatur_gueltig

logger = logging.getLogger(__name__)

WEBHOOK_PFAD = "/webhook/whatsapp"


@asynccontextmanager
async def lebenszyklus(app: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    logger.info("Bautics gestartet.")
    yield


app = FastAPI(
    title="Bautics",
    description="Echo: Sprachnachricht des Bauleiters wird zum Tagesbericht.",
    version="0.1.0",
    lifespan=lebenszyklus,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _twiml(text: str) -> Response:
    """Twilio erwartet TwiML - eine Nachricht zurueck an den Absender."""
    koerper = f"<?xml version='1.0' encoding='UTF-8'?><Response><Message>{escape(text)}</Message></Response>"
    return Response(content=koerper, media_type="application/xml")


def _signierte_url(request: Request) -> str:
    """Die URL, die Twilio signiert hat.

    Hinter Proxy/Loadbalancer stimmt ``request.url`` im Schema oft nicht mehr,
    deshalb bauen wir sie aus der konfigurierten oeffentlichen Basis-URL.
    """
    url = f"{config.BASE_URL}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    return url


@app.post(WEBHOOK_PFAD)
async def whatsapp_webhook(request: Request, hintergrund: BackgroundTasks) -> Response:
    formular = await request.form()
    felder = {schluessel: str(wert) for schluessel, wert in formular.items()}

    if config.TWILIO_SIGNATUR_PRUEFEN:
        signatur = request.headers.get("X-Twilio-Signature", "")
        if not signatur_gueltig(_signierte_url(request), felder, signatur):
            logger.warning("Webhook mit ungueltiger Signatur abgewiesen.")
            return Response(status_code=status.HTTP_403_FORBIDDEN)

    nachricht = EingehendeNachricht.aus_twilio_formular(felder)
    if not nachricht.nachricht_sid:
        logger.warning("Webhook ohne MessageSid abgewiesen.")
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    logger.info(
        "Webhook von %s (Audio: %s).",
        maskiere_nummer(nachricht.absender),
        nachricht.hat_audio,
    )

    annahme = nimm_nachricht_an(nachricht)
    if annahme.verarbeiten and annahme.bericht_id and annahme.medien_url:
        # Spracherkennung und Engine dauern laenger als Twilios Geduld:
        # sofort quittieren, Bericht danach im Hintergrund erstellen.
        hintergrund.add_task(
            _hintergrund_verarbeitung, annahme.bericht_id, annahme.medien_url
        )

    return _twiml(annahme.antwort_text)


def _hintergrund_verarbeitung(bericht_id: int, medien_url: str) -> None:
    """Hintergrundlauf - Fehler sind bereits am Bericht vermerkt und dem
    Bauleiter gemeldet, sie duerfen den Server nicht weiter beschaeftigen."""
    try:
        verarbeite_bericht(bericht_id, medien_url)
    except Exception:  # noqa: BLE001
        logger.warning("Bericht %s konnte nicht erstellt werden.", bericht_id)

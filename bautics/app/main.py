"""FastAPI-Einstiegspunkt.

Der HTTP-Layer macht hier ausschliesslich drei Dinge: Zugang pruefen,
Eingaben in ein Fachobjekt uebersetzen und die Antwort formulieren. Die
eigentliche Logik steht in ``echo.py`` (Echo) und ``mind.py`` (Mind) und ist
ohne HTTP testbar.
"""

import logging
from contextlib import asynccontextmanager
from hmac import compare_digest
from typing import AsyncIterator
from xml.sax.saxutils import escape

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, Field

from . import config, db, mind
from .echo import (
    EingehendeNachricht,
    maskiere_nummer,
    nimm_nachricht_an,
    verarbeite_bericht,
)
from .http_client import UpstreamFehler
from .openrouter import EngineFehler
from .schemas import Fundstelle
from .twilio_api import signatur_gueltig

logger = logging.getLogger(__name__)

WEBHOOK_PFAD = "/webhook/whatsapp"


@asynccontextmanager
async def lebenszyklus(app: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    if not config.API_TOKEN:
        logger.warning(
            "BAUTICS_API_TOKEN ist nicht gesetzt - die /mind-Routen sind ungeschuetzt. "
            "Fuer den Betrieb mit Kundendokumenten ist das nicht zulaessig."
        )
    logger.info("Bautics gestartet.")
    yield


app = FastAPI(
    title="Bautics",
    description=(
        "Echo: Sprachnachricht des Bauleiters wird zum Tagesbericht. "
        "Mind: Fragen an die Projektunterlagen - nur mit Fundstelle."
    ),
    version="0.2.0",
    lifespan=lebenszyklus,
)


def pruefe_zugang(request: Request) -> None:
    """Bearer-Token der /mind-Routen.

    Ist kein Token konfiguriert, bleiben die Routen offen - vertretbar nur
    lokal; beim Start wird deshalb gewarnt.
    """
    if not config.API_TOKEN:
        return
    kopf = request.headers.get("Authorization", "")
    schema, _, wert = kopf.partition(" ")
    if schema.lower() != "bearer" or not compare_digest(wert.strip(), config.API_TOKEN):
        logger.warning("Zugriff auf %s ohne gueltiges Token.", request.url.path)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Nicht berechtigt.")


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


# --- Mind: Wissensbank ------------------------------------------------------


class FrageAnfrage(BaseModel):
    """Frage an die Wissensbank, optional auf ein Baulos eingegrenzt."""

    frage: str = Field(min_length=3, max_length=2000)
    baulos: str | None = Field(
        default=None,
        max_length=200,
        description="Nur Unterlagen dieses Bauloses durchsuchen",
    )


class FrageAntwort(BaseModel):
    """Antwort der Wissensbank - ohne Fundstellen ist ``gefunden`` false."""

    gefunden: bool
    antwort: str
    fundstellen: list[Fundstelle]


@app.post("/mind/frage", response_model=FrageAntwort, dependencies=[Depends(pruefe_zugang)])
def mind_frage(anfrage: FrageAnfrage) -> FrageAntwort:
    """Frage beantworten - ausschliesslich belegt aus den Projektunterlagen."""
    try:
        ergebnis = mind.beantworte_frage(anfrage.frage, baulos=anfrage.baulos)
    except (EngineFehler, UpstreamFehler) as fehler:
        # Ein Ausfall darf nicht als "dazu finde ich nichts" erscheinen - das
        # waere eine Aussage ueber die Unterlagen, die niemand geprueft hat.
        logger.warning("Frage konnte nicht beantwortet werden: %s", type(fehler).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Die Auskunft ist derzeit nicht moeglich. Bitte spaeter erneut versuchen.",
        ) from fehler
    # Der interne Verwerfungsgrund bleibt im Audit-Log; nach aussen geht nur
    # die Antwort selbst.
    return FrageAntwort(
        gefunden=ergebnis.gefunden,
        antwort=ergebnis.antwort,
        fundstellen=ergebnis.fundstellen,
    )


@app.post(
    "/mind/indexieren",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(pruefe_zugang)],
)
def mind_indexieren(hintergrund: BackgroundTasks) -> dict[str, str]:
    """Indexlauf ueber den Wissensbank-Ordner anstossen.

    Einlesen und Einbetten dauert je nach Bestand Minuten - deshalb im
    Hintergrund. Das Ergebnis ist ueber ``GET /mind/status`` sichtbar.
    """
    hintergrund.add_task(_hintergrund_indexierung)
    return {"status": "Indexierung gestartet."}


@app.get("/mind/status", dependencies=[Depends(pruefe_zugang)])
def mind_status() -> dict[str, object]:
    """Kennzahlen des Index - keine Dokumentinhalte."""
    return mind.wissensbank_status()


def _hintergrund_indexierung() -> None:
    """Indexlauf im Hintergrund - Fehler stehen am jeweiligen Dokument."""
    try:
        mind.indexiere_wissensbank()
    except Exception:  # noqa: BLE001
        logger.exception("Indexlauf abgebrochen.")

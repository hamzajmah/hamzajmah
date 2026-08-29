"""Twilio-Anbindung: Signaturpruefung, Medien-Download, WhatsApp-Antwort.

Bewusst ohne das Twilio-SDK - es sind zwei HTTP-Aufrufe und eine HMAC-Pruefung,
die wir so ueber unseren gemeinsamen Retry-Pfad fuehren koennen.
"""

import base64
import hashlib
import hmac
import logging
from typing import Iterable, Mapping
from urllib.parse import urlsplit

import httpx

from . import config
from .http_client import UpstreamFehler, anfrage_mit_retry

logger = logging.getLogger(__name__)

DIENST = "Twilio"
# WhatsApp nimmt maximal 1600 Zeichen je Nachricht an.
MAX_NACHRICHTENLAENGE = 1500


class TwilioFehler(RuntimeError):
    """Ein Twilio-Aufruf ist endgueltig fehlgeschlagen."""


def signatur_gueltig(url: str, formularfelder: Mapping[str, str], signatur: str) -> bool:
    """Prueft die X-Twilio-Signature eines eingehenden Webhooks.

    Twilio bildet HMAC-SHA1 ueber die vollstaendige URL plus alle
    Formularfelder in alphabetischer Reihenfolge (Schluessel und Wert
    unmittelbar aneinandergehaengt).
    """
    if not signatur or not config.TWILIO_AUTH_TOKEN:
        return False

    rohwert = url + "".join(
        f"{schluessel}{formularfelder[schluessel]}" for schluessel in sorted(formularfelder)
    )
    erwartet = base64.b64encode(
        hmac.new(
            config.TWILIO_AUTH_TOKEN.encode("utf-8"),
            rohwert.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("ascii")
    return hmac.compare_digest(erwartet, signatur)


def _ist_twilio_host(url: str) -> bool:
    """Nur Twilio-Hosts bekommen unsere Zugangsdaten zu sehen.

    Ohne diese Pruefung koennte eine manipulierte Medien-URL den Server dazu
    bringen, das Twilio-Token an einen fremden Server zu schicken.
    """
    host = (urlsplit(url).hostname or "").lower()
    erlaubter_host = (urlsplit(config.TWILIO_API_BASE_URL).hostname or "").lower()
    return host == erlaubter_host or host.endswith(".twilio.com")


def _basis_auth() -> tuple[str, str]:
    if not (config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN):
        raise TwilioFehler("TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN sind nicht gesetzt.")
    return (config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)


def lade_medium(
    medien_url: str,
    *,
    client: httpx.Client | None = None,
) -> tuple[bytes, str]:
    """Laedt einen Medienanhang und liefert (Bytes, Content-Type).

    Twilio antwortet auf die Medien-URL mit einer Weiterleitung auf den
    eigentlichen Speicherort. Diese Weiterleitung holen wir bewusst ohne
    Zugangsdaten ab, damit unser Twilio-Token nicht an ein CDN geht.
    """
    if not medien_url.startswith("https://"):
        raise TwilioFehler("Medien-URL ist nicht HTTPS.")
    if not _ist_twilio_host(medien_url):
        raise TwilioFehler("Medien-URL zeigt nicht auf Twilio.")

    eigener_client = client is None
    aktiver_client = client or httpx.Client(timeout=config.HTTP_TIMEOUT_SEKUNDEN)
    try:
        antwort = anfrage_mit_retry(
            DIENST,
            lambda: aktiver_client.get(
                medien_url, auth=_basis_auth(), follow_redirects=False
            ),
        )
        if antwort.status_code in (301, 302, 303, 307, 308):
            ziel = antwort.headers.get("location", "")
            if not ziel.startswith("https://"):
                raise TwilioFehler("Weiterleitung ohne gueltiges HTTPS-Ziel erhalten.")
            antwort = anfrage_mit_retry(
                DIENST,
                lambda: aktiver_client.get(ziel, follow_redirects=True),
            )
    except UpstreamFehler as fehler:
        raise TwilioFehler(str(fehler)) from fehler
    finally:
        if eigener_client:
            aktiver_client.close()

    inhaltstyp = antwort.headers.get("content-type", "application/octet-stream").split(";")[0]
    logger.info("Medium geladen (%s Bytes, %s).", len(antwort.content), inhaltstyp)
    return antwort.content, inhaltstyp


def teile_text(text: str, laenge: int = MAX_NACHRICHTENLAENGE) -> list[str]:
    """Langen Berichtstext in versandfaehige Haeppchen schneiden."""
    if len(text) <= laenge:
        return [text]

    teile: list[str] = []
    rest = text
    while len(rest) > laenge:
        schnitt = rest.rfind("\n", 0, laenge)
        if schnitt <= 0:
            schnitt = laenge
        teile.append(rest[:schnitt].rstrip())
        rest = rest[schnitt:].lstrip("\n")
    if rest:
        teile.append(rest)
    return teile


def sende_whatsapp(
    an: str,
    text: str,
    *,
    client: httpx.Client | None = None,
) -> None:
    """Schickt eine (ggf. mehrteilige) WhatsApp-Nachricht an den Bauleiter."""
    eigener_client = client is None
    aktiver_client = client or httpx.Client(timeout=config.HTTP_TIMEOUT_SEKUNDEN)
    url = (
        f"{config.TWILIO_API_BASE_URL}/2010-04-01/Accounts/"
        f"{config.TWILIO_ACCOUNT_SID}/Messages.json"
    )
    try:
        for abschnitt in _nicht_leer(teile_text(text)):
            anfrage_mit_retry(
                DIENST,
                lambda abschnitt=abschnitt: aktiver_client.post(
                    url,
                    auth=_basis_auth(),
                    data={
                        "From": config.TWILIO_WHATSAPP_FROM,
                        "To": an,
                        "Body": abschnitt,
                    },
                ),
            )
    except UpstreamFehler as fehler:
        raise TwilioFehler(str(fehler)) from fehler
    finally:
        if eigener_client:
            aktiver_client.close()
    logger.info("WhatsApp-Antwort versendet (%s Zeichen).", len(text))


def _nicht_leer(abschnitte: Iterable[str]) -> list[str]:
    return [abschnitt for abschnitt in abschnitte if abschnitt.strip()]

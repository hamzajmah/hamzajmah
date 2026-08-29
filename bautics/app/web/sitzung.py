"""Zugang zur Oberflaeche.

Entwurfsentscheidung: Es gibt genau ein Geheimnis, ``BAUTICS_API_TOKEN`` -
dasselbe, das die JSON-Schnittstelle schuetzt. Wer es kennt, gibt es einmal
im Anmeldeformular ein und bekommt ein signiertes Sitzungs-Cookie; das Token
selbst wandert nicht ins Cookie. Damit ist der Login kein Schaufenster: ohne
gueltiges Token kommt niemand an Kundendokumente.

Bewusst *keine* Benutzerverwaltung - die ist nicht Teil dieses Auftrags. Der
Weg dorthin bleibt offen: Im Cookie steht bereits ein Feld ``sub`` (Subjekt),
das heute konstant "token" ist und spaeter eine Benutzer-ID aufnehmen kann,
ohne dass sich Cookie-Format oder Aufrufer aendern.

Ist kein Token konfiguriert, gibt es kein Anmeldeformular: Ein Login, der
alles durchlaesst, taeuscht Schutz nur vor. Stattdessen laeuft die
Oberflaeche wie die Schnittstelle offen und zeigt auf jeder Seite ein
Warnband. Zulaessig ist das nur lokal.
"""

import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import threading
from typing import Optional

from fastapi import Request, Response

from .. import config

logger = logging.getLogger(__name__)

COOKIE_NAME = "bautics_sitzung"
# Versionskennung im Cookie: aendert sich das Format, werden alte Cookies
# nicht stillschweigend fehlinterpretiert, sondern verworfen.
FORMAT_VERSION = 1
_SCHLUESSEL_KONTEXT = b"bautics-ui-sitzung-v1"


class NichtAngemeldet(Exception):
    """Wird von ``verlange_anmeldung`` geworfen und oben in eine
    Weiterleitung auf das Anmeldeformular uebersetzt."""


def schutz_aktiv() -> bool:
    """Ist ein Token konfiguriert - gibt es also ueberhaupt etwas zu pruefen?"""
    return bool(config.API_TOKEN)


def _schluessel() -> bytes:
    """Signierschluessel, aus dem Token abgeleitet - nicht das Token selbst.

    Folge: Wird das Token gewechselt, sind alle laufenden Sitzungen sofort
    ungueltig. Das ist gewollt.
    """
    return hmac.new(_SCHLUESSEL_KONTEXT, config.API_TOKEN.encode("utf-8"), hashlib.sha256).digest()


def _b64(rohdaten: bytes) -> str:
    return base64.urlsafe_b64encode(rohdaten).decode("ascii").rstrip("=")


def _entb64(text: str) -> bytes:
    fehlend = (-len(text)) % 4
    return base64.urlsafe_b64decode(text + "=" * fehlend)


def baue_cookie(jetzt: Optional[dt.datetime] = None) -> str:
    """Signierten Cookie-Wert erzeugen: ``nutzlast.signatur``."""
    jetzt = jetzt or dt.datetime.now(dt.timezone.utc)
    ablauf = jetzt + dt.timedelta(hours=config.UI_SITZUNG_STUNDEN)
    nutzlast = json.dumps(
        {"v": FORMAT_VERSION, "sub": "token", "exp": int(ablauf.timestamp())},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    teil = _b64(nutzlast)
    signatur = hmac.new(_schluessel(), teil.encode("ascii"), hashlib.sha256).digest()
    return f"{teil}.{_b64(signatur)}"


def cookie_gueltig(wert: str, jetzt: Optional[dt.datetime] = None) -> bool:
    """Signatur und Ablauf pruefen. Alles Unklare gilt als ungueltig."""
    if not schutz_aktiv() or not wert:
        return False
    teil, punkt, signatur_teil = wert.partition(".")
    if not punkt:
        return False
    erwartet = hmac.new(_schluessel(), teil.encode("ascii"), hashlib.sha256).digest()
    try:
        geliefert = _entb64(signatur_teil)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return False
    if not hmac.compare_digest(erwartet, geliefert):
        return False
    try:
        nutzlast = json.loads(_entb64(teil))
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return False
    if not isinstance(nutzlast, dict) or nutzlast.get("v") != FORMAT_VERSION:
        return False
    ablauf = nutzlast.get("exp")
    if not isinstance(ablauf, int):
        return False
    jetzt = jetzt or dt.datetime.now(dt.timezone.utc)
    return jetzt.timestamp() < ablauf


def ist_angemeldet(request: Request) -> bool:
    """Ohne konfiguriertes Token ist die Oberflaeche offen (nur lokal!)."""
    if not schutz_aktiv():
        return True
    return cookie_gueltig(request.cookies.get(COOKIE_NAME, ""))


def verlange_anmeldung(request: Request) -> None:
    """FastAPI-Abhaengigkeit fuer alle geschuetzten Seiten."""
    if not ist_angemeldet(request):
        raise NichtAngemeldet()


def setze_cookie(antwort: Response) -> None:
    antwort.set_cookie(
        COOKIE_NAME,
        baue_cookie(),
        max_age=config.UI_SITZUNG_STUNDEN * 3600,
        httponly=True,
        # "lax" verhindert, dass fremde Seiten Formulare gegen uns absenden -
        # deshalb brauchen die Formulare hier kein eigenes CSRF-Token.
        samesite="lax",
        secure=config.BASE_URL.startswith("https://"),
        path="/",
    )


def loesche_cookie(antwort: Response) -> None:
    antwort.delete_cookie(COOKIE_NAME, path="/")


def token_stimmt(eingabe: str) -> bool:
    """Zeitkonstanter Vergleich - kein frueher Abbruch beim ersten Zeichen."""
    if not schutz_aktiv():
        return False
    return hmac.compare_digest(eingabe.strip(), config.API_TOKEN)


# --- Bremse gegen das Durchprobieren des Tokens ----------------------------

# Absichtlich im Prozessspeicher: eine Zeile Zustand statt einer weiteren
# Abhaengigkeit. Bei mehreren Arbeitsprozessen zaehlt jeder fuer sich - die
# Bremse wird dann lascher, aber nie falsch. Fuer mehr braucht es Redis
# oder einen Reverse-Proxy davor.
_versuche: dict[str, tuple[int, float]] = {}
_sperre = threading.Lock()


def _jetzt_sekunden() -> float:
    return dt.datetime.now(dt.timezone.utc).timestamp()


def darf_versuchen(kennung: str) -> bool:
    with _sperre:
        anzahl, bis = _versuche.get(kennung, (0, 0.0))
        if bis and bis <= _jetzt_sekunden():
            _versuche.pop(kennung, None)
            return True
        return anzahl < config.UI_ANMELDE_VERSUCHE


def vermerke_fehlversuch(kennung: str) -> None:
    with _sperre:
        anzahl, _ = _versuche.get(kennung, (0, 0.0))
        _versuche[kennung] = (anzahl + 1, _jetzt_sekunden() + config.UI_ANMELDE_SPERRE_SEKUNDEN)


def loesche_versuche(kennung: str) -> None:
    with _sperre:
        _versuche.pop(kennung, None)


def anfrage_kennung(request: Request) -> str:
    """Grobe Herkunft fuer die Bremse - keine Personendaten, nur die IP."""
    return request.client.host if request.client else "unbekannt"

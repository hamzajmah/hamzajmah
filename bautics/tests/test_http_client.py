"""Retries mit Backoff und die Twilio-Hilfsfunktionen."""

import base64
import hashlib
import hmac

import httpx
import pytest

from app import config
from app.http_client import UpstreamFehler, anfrage_mit_retry
from app.twilio_api import TwilioFehler, lade_medium, signatur_gueltig, teile_text

FELDER = {
    "MessageSid": "SM1111",
    "From": "whatsapp:+4915112345678",
    "NumMedia": "1",
}
URL = "https://echo.bautics.test/webhook/whatsapp"


def test_serverfehler_wird_wiederholt():
    versuche: list[int] = []
    pausen: list[float] = []

    def aufruf() -> httpx.Response:
        versuche.append(1)
        if len(versuche) < 3:
            return httpx.Response(503, request=httpx.Request("GET", URL))
        return httpx.Response(200, request=httpx.Request("GET", URL))

    antwort = anfrage_mit_retry("Test", aufruf, schlafen=pausen.append)

    assert antwort.status_code == 200
    assert len(versuche) == 3
    # Exponentiell: 1s, dann 2s
    assert pausen == [config.HTTP_BACKOFF_BASIS_SEKUNDEN, config.HTTP_BACKOFF_BASIS_SEKUNDEN * 2]


def test_clientfehler_wird_nicht_wiederholt():
    versuche: list[int] = []

    def aufruf() -> httpx.Response:
        versuche.append(1)
        return httpx.Response(401, request=httpx.Request("GET", URL))

    with pytest.raises(UpstreamFehler) as fehler:
        anfrage_mit_retry("Test", aufruf, schlafen=lambda _: None)

    assert fehler.value.status == 401
    assert len(versuche) == 1


def test_netzfehler_endet_nach_maximalen_versuchen():
    versuche: list[int] = []

    def aufruf() -> httpx.Response:
        versuche.append(1)
        raise httpx.ConnectError("kein Netz")

    with pytest.raises(UpstreamFehler):
        anfrage_mit_retry("Test", aufruf, max_versuche=2, schlafen=lambda _: None)

    assert len(versuche) == 2


def test_twilio_signatur():
    # Signatur nach Twilio-Vorschrift: URL + alphabetisch sortierte Formularfelder
    rohwert = URL + "".join(f"{k}{FELDER[k]}" for k in sorted(FELDER))
    signatur = base64.b64encode(
        hmac.new(config.TWILIO_AUTH_TOKEN.encode(), rohwert.encode(), hashlib.sha1).digest()
    ).decode()

    assert signatur_gueltig(URL, FELDER, signatur)
    assert not signatur_gueltig(URL, FELDER, "offensichtlich falsch")
    assert not signatur_gueltig(URL, FELDER, "")
    # Ein veraendertes Feld macht die Signatur ungueltig
    assert not signatur_gueltig(URL, dict(FELDER, NumMedia="2"), signatur)


def test_medien_download_folgt_weiterleitung_ohne_zugangsdaten():
    gesehen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.append(request)
        if request.url.host == "api.twilio.com":
            return httpx.Response(307, headers={"location": "https://cdn.example/datei.ogg"})
        return httpx.Response(200, content=b"audiobytes", headers={"content-type": "audio/ogg"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        inhalt, typ = lade_medium("https://api.twilio.com/media/1", client=client)

    assert inhalt == b"audiobytes"
    assert typ == "audio/ogg"
    assert "authorization" in gesehen[0].headers
    # Der zweite Abruf geht ohne unser Twilio-Token an das CDN.
    assert "authorization" not in gesehen[1].headers


def test_fremde_medien_url_bekommt_keine_zugangsdaten():
    """Manipulierte Medien-URL darf unser Twilio-Token nicht abgreifen."""
    with pytest.raises(TwilioFehler):
        lade_medium("https://boeser-host.example/media/1")
    with pytest.raises(TwilioFehler):
        lade_medium("http://api.twilio.com/media/1")


def test_langer_bericht_wird_geteilt():
    text = "\n".join(f"Zeile {nummer}" for nummer in range(400))
    teile = teile_text(text, laenge=200)

    assert len(teile) > 1
    assert all(len(teil) <= 200 for teil in teile)
    assert "".join(teile).replace("\n", "") == text.replace("\n", "")

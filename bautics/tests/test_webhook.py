"""HTTP-Schicht: Signaturpruefung und TwiML-Antwort."""

import base64
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.echo import ANTWORT_ANGENOMMEN, ANTWORT_KEINE_AUDIODATEI

FORMULAR = {
    "MessageSid": "SM-webhook-1",
    "From": "whatsapp:+4915112345678",
    "NumMedia": "1",
    "MediaUrl0": "https://api.twilio.com/media/1",
    "MediaContentType0": "audio/ogg",
    "Body": "",
}


def _signatur(url: str, felder: dict[str, str]) -> str:
    rohwert = url + "".join(f"{k}{felder[k]}" for k in sorted(felder))
    return base64.b64encode(
        hmac.new(
            config.TWILIO_AUTH_TOKEN.encode(), rohwert.encode(), hashlib.sha1
        ).digest()
    ).decode()


@pytest.fixture
def client(datenbank, monkeypatch):
    """Testclient mit abgefangener Hintergrundverarbeitung."""
    aufrufe: list[tuple[int, str]] = []
    monkeypatch.setattr(
        main,
        "verarbeite_bericht",
        lambda bericht_id, medien_url: aufrufe.append((bericht_id, medien_url)),
    )
    with TestClient(main.app) as testclient:
        testclient.hintergrund_aufrufe = aufrufe
        yield testclient


def test_gueltige_signatur_wird_angenommen(client, monkeypatch):
    monkeypatch.setattr(config, "TWILIO_SIGNATUR_PRUEFEN", True)
    url = f"{config.BASE_URL}{main.WEBHOOK_PFAD}"

    antwort = client.post(
        main.WEBHOOK_PFAD,
        data=FORMULAR,
        headers={"X-Twilio-Signature": _signatur(url, FORMULAR)},
    )

    assert antwort.status_code == 200
    assert "<Response><Message>" in antwort.text
    assert ANTWORT_ANGENOMMEN in antwort.text
    assert len(client.hintergrund_aufrufe) == 1
    assert client.hintergrund_aufrufe[0][1] == FORMULAR["MediaUrl0"]


def test_falsche_signatur_wird_abgewiesen(client, monkeypatch):
    monkeypatch.setattr(config, "TWILIO_SIGNATUR_PRUEFEN", True)

    antwort = client.post(
        main.WEBHOOK_PFAD, data=FORMULAR, headers={"X-Twilio-Signature": "falsch"}
    )

    assert antwort.status_code == 403
    assert client.hintergrund_aufrufe == []


def test_fehlende_signatur_wird_abgewiesen(client, monkeypatch):
    monkeypatch.setattr(config, "TWILIO_SIGNATUR_PRUEFEN", True)

    antwort = client.post(main.WEBHOOK_PFAD, data=FORMULAR)

    assert antwort.status_code == 403


def test_textnachricht_ohne_audio(client, monkeypatch):
    monkeypatch.setattr(config, "TWILIO_SIGNATUR_PRUEFEN", False)

    antwort = client.post(
        main.WEBHOOK_PFAD,
        data={"MessageSid": "SM-webhook-2", "From": FORMULAR["From"], "NumMedia": "0"},
    )

    assert antwort.status_code == 200
    assert ANTWORT_KEINE_AUDIODATEI.split("–")[0].strip() in antwort.text
    assert client.hintergrund_aufrufe == []


def test_webhook_ohne_message_sid(client, monkeypatch):
    monkeypatch.setattr(config, "TWILIO_SIGNATUR_PRUEFEN", False)

    antwort = client.post(main.WEBHOOK_PFAD, data={"From": FORMULAR["From"]})

    assert antwort.status_code == 400


def test_wiederholte_zustellung_startet_keine_zweite_verarbeitung(client, monkeypatch):
    monkeypatch.setattr(config, "TWILIO_SIGNATUR_PRUEFEN", False)

    client.post(main.WEBHOOK_PFAD, data=FORMULAR)
    zweite = client.post(main.WEBHOOK_PFAD, data=FORMULAR)

    assert zweite.status_code == 200
    assert len(client.hintergrund_aufrufe) == 1


def test_health():
    with TestClient(main.app) as testclient:
        assert testclient.get("/health").json() == {"status": "ok"}

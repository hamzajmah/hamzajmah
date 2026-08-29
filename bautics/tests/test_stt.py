"""Spracherkennung - Provider gemockt, Kontext aus dem Bau-Glossar geprueft."""

import httpx
import pytest

from app import config
from app.glossary import STT_KONTEXT
from app.stt import TranskriptionsFehler, transkribiere


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_whisper_bekommt_das_bau_glossar_als_kontext(monkeypatch):
    monkeypatch.setattr(config, "STT_PROVIDER", "openai")
    gesehen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.append(request)
        return httpx.Response(200, json={"text": "Rohrgraben bis Station 12+700."})

    with _client(handler) as client:
        text = transkribiere(b"audiobytes", "audio/ogg", client=client)

    assert text == "Rohrgraben bis Station 12+700."
    anfrage = gesehen[0]
    assert str(anfrage.url).endswith("/audio/transcriptions")
    koerper = anfrage.content.decode("utf-8", errors="ignore")
    assert STT_KONTEXT in koerper
    assert "Stationierung" in koerper
    assert config.STT_MODEL_OPENAI in koerper
    assert "sprachnachricht.ogg" in koerper


def test_deepgram_bekommt_die_glossarbegriffe(monkeypatch):
    monkeypatch.setattr(config, "STT_PROVIDER", "deepgram")
    gesehen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        gesehen.append(request)
        return httpx.Response(
            200,
            json={
                "results": {
                    "channels": [{"alternatives": [{"transcript": "Kabelzug erledigt."}]}]
                }
            },
        )

    with _client(handler) as client:
        text = transkribiere(b"audiobytes", "audio/mpeg", client=client)

    assert text == "Kabelzug erledigt."
    assert "keywords=Stationierung" in str(gesehen[0].url)
    assert gesehen[0].headers["content-type"] == "audio/mpeg"


def test_leeres_audio_wird_abgelehnt():
    with pytest.raises(TranskriptionsFehler):
        transkribiere(b"")


def test_zu_grosse_datei_wird_abgelehnt(monkeypatch):
    monkeypatch.setattr(config, "MAX_AUDIO_BYTES", 10)
    with pytest.raises(TranskriptionsFehler):
        transkribiere(b"x" * 11)


def test_unbekannter_provider_faellt_auf(monkeypatch):
    monkeypatch.setattr(config, "STT_PROVIDER", "irgendwas")
    with pytest.raises(TranskriptionsFehler):
        transkribiere(b"audiobytes", "audio/ogg")


def test_leeres_transkript_ist_ein_fehler(monkeypatch):
    monkeypatch.setattr(config, "STT_PROVIDER", "openai")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "   "})

    with _client(handler) as client:
        with pytest.raises(TranskriptionsFehler):
            transkribiere(b"audiobytes", "audio/ogg", client=client)

"""Embeddings ueber denselben Engine-Zugang - ohne echten Aufruf."""

import json

import httpx
import pytest

from app import config
from app.openrouter import EngineFehler, erzeuge_vektoren


def _client(antwort_bauer, aufzeichnung: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        aufzeichnung.append(request)
        return httpx.Response(200, json=antwort_bauer(json.loads(request.content)))

    return httpx.Client(transport=httpx.MockTransport(handler))


def _vektor(startwert: float) -> list[float]:
    return [startwert] * config.EMBEDDING_DIMENSIONEN


def test_vektoren_behalten_die_reihenfolge_der_texte():
    """Die API darf umsortieren - die Zuordnung Chunk/Vektor darf nicht kippen."""
    aufzeichnung: list[httpx.Request] = []

    def antwort(nutzlast: dict) -> dict:
        # Absichtlich verdrehte Reihenfolge.
        return {
            "data": [
                {"index": 1, "embedding": _vektor(2.0)},
                {"index": 0, "embedding": _vektor(1.0)},
            ]
        }

    with _client(antwort, aufzeichnung) as client:
        vektoren = erzeuge_vektoren(["erster", "zweiter"], modell="test/modell", client=client)

    assert vektoren[0][0] == 1.0
    assert vektoren[1][0] == 2.0


def test_anfrage_traegt_datenschutz_routing():
    aufzeichnung: list[httpx.Request] = []

    with _client(lambda _: {"data": [{"index": 0, "embedding": _vektor(1.0)}]}, aufzeichnung) as client:
        erzeuge_vektoren(["ein Text"], modell="test/modell", client=client)

    anfrage = aufzeichnung[0]
    assert str(anfrage.url).endswith("/embeddings")
    nutzlast = json.loads(anfrage.content)
    assert nutzlast["provider"] == {
        "zdr": True,
        "data_collection": "deny",
        "require_parameters": True,
    }
    assert nutzlast["model"] == "test/modell"


def test_grosse_mengen_werden_gestueckelt(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_BATCH", 2)
    aufzeichnung: list[httpx.Request] = []

    def antwort(nutzlast: dict) -> dict:
        return {
            "data": [
                {"index": index, "embedding": _vektor(1.0)}
                for index in range(len(nutzlast["input"]))
            ]
        }

    with _client(antwort, aufzeichnung) as client:
        vektoren = erzeuge_vektoren(["a", "b", "c"], modell="test/modell", client=client)

    assert len(vektoren) == 3
    assert len(aufzeichnung) == 2


def test_falsche_vektorlaenge_wird_gemeldet():
    """Sonst landen unbrauchbare Vektoren in der Datenbank."""
    aufzeichnung: list[httpx.Request] = []

    with _client(lambda _: {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}, aufzeichnung) as client:
        with pytest.raises(EngineFehler):
            erzeuge_vektoren(["ein Text"], modell="test/modell", client=client)


def test_fehlender_vektor_wird_gemeldet():
    aufzeichnung: list[httpx.Request] = []

    with _client(lambda _: {"data": []}, aufzeichnung) as client:
        with pytest.raises(EngineFehler):
            erzeuge_vektoren(["ein Text"], modell="test/modell", client=client)


def test_leere_eingabe_erzeugt_keinen_aufruf():
    aufzeichnung: list[httpx.Request] = []

    with _client(lambda _: {"data": []}, aufzeichnung) as client:
        assert erzeuge_vektoren([], modell="test/modell", client=client) == []

    assert aufzeichnung == []

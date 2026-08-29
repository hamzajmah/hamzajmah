"""HTTP-Schicht von Mind: Frage-Route, Indexierung, Zugangsschutz."""

import pytest
from fastapi.testclient import TestClient

from app import config, main, mind
from app.mind import ANTWORT_NICHTS_GEFUNDEN, MindErgebnis
from app.openrouter import EngineFehler
from app.schemas import Fundstelle


@pytest.fixture
def klient(datenbank):
    with TestClient(main.app) as client:
        yield client


def test_frage_liefert_antwort_mit_fundstellen(klient, monkeypatch):
    def antwort(frage: str, *, baulos=None, dienste=None) -> MindErgebnis:
        assert frage == "Wer traegt die Wasserhaltung?"
        assert baulos == "SuedLink Baulos 4"
        return MindErgebnis(
            gefunden=True,
            antwort="Der Auftragnehmer.",
            fundstellen=[
                Fundstelle(
                    datei="LV_Los4.pdf",
                    seite=4,
                    abschnitt="4 Wasserhaltung",
                    zitat="Die Wasserhaltung ist Nebenleistung",
                )
            ],
            chunk_ids=[1],
        )

    monkeypatch.setattr(mind, "beantworte_frage", antwort)

    ergebnis = klient.post(
        "/mind/frage",
        json={"frage": "Wer traegt die Wasserhaltung?", "baulos": "SuedLink Baulos 4"},
    )

    assert ergebnis.status_code == 200
    daten = ergebnis.json()
    assert daten["gefunden"] is True
    assert daten["fundstellen"][0]["seite"] == 4


def test_verworfene_antwort_kommt_ohne_innenansicht_zurueck(klient, monkeypatch):
    """Der Verwerfungsgrund gehoert ins Audit-Log, nicht in die Antwort."""

    def antwort(frage: str, *, baulos=None, dienste=None) -> MindErgebnis:
        return MindErgebnis(
            gefunden=False,
            antwort=ANTWORT_NICHTS_GEFUNDEN,
            fundstellen=[],
            chunk_ids=[7],
            hinweis=mind.GRUND_ZITAT_NICHT_GEFUNDEN,
        )

    monkeypatch.setattr(mind, "beantworte_frage", antwort)

    daten = klient.post("/mind/frage", json={"frage": "Gibt es Nachtraege?"}).json()

    assert daten == {
        "gefunden": False,
        "antwort": ANTWORT_NICHTS_GEFUNDEN,
        "fundstellen": [],
    }
    assert "hinweis" not in daten


def test_zu_kurze_frage_wird_abgewiesen(klient):
    assert klient.post("/mind/frage", json={"frage": "?"}).status_code == 422


def test_status_route_meldet_kennzahlen(klient):
    daten = klient.get("/mind/status").json()

    assert daten["dokumente"] == 0
    assert daten["chunks"] == 0


def test_indexierung_wird_angestossen(klient, monkeypatch):
    laeufe: list[bool] = []
    monkeypatch.setattr(mind, "indexiere_wissensbank", lambda: laeufe.append(True))

    antwort = klient.post("/mind/indexieren")

    assert antwort.status_code == 202
    # BackgroundTasks laufen beim TestClient nach der Antwort.
    assert laeufe == [True]


def test_ohne_token_kein_zugriff(datenbank, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "geheim")
    with TestClient(main.app) as klient:
        assert klient.get("/mind/status").status_code == 401
        assert (
            klient.get("/mind/status", headers={"Authorization": "Bearer falsch"}).status_code
            == 401
        )
        assert (
            klient.get("/mind/status", headers={"Authorization": "Bearer geheim"}).status_code
            == 200
        )


def test_engine_ausfall_wird_als_stoerung_gemeldet(klient, monkeypatch):
    """503 statt einer falschen Auskunft, es stehe nichts in den Unterlagen."""

    def antwort(frage: str, *, baulos=None, dienste=None):
        raise EngineFehler("Engine weg")

    monkeypatch.setattr(mind, "beantworte_frage", antwort)

    ergebnis = klient.post("/mind/frage", json={"frage": "Gibt es Nachtraege?"})

    assert ergebnis.status_code == 503
    assert ANTWORT_NICHTS_GEFUNDEN not in ergebnis.text

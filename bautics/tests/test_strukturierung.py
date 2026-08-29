"""Structured-Output-Pipeline - ohne echten Modellaufruf.

Kernfrage: Was der Bauleiter nicht gesagt hat, muss leer bleiben.
"""

import json

import httpx
import pytest

from app import config
from app.openrouter import EngineFehler, strict_json_schema
from app.schemas import TagesberichtDaten
from app.strukturierung import SYSTEM_PROMPT, strukturiere_transkript

TRANSKRIPT_OHNE_GERAETE = (
    "Heute waren wir mit acht Mann im Abschnitt. Wir haben den Rohrgraben von "
    "Station zwoelf plus vierhundert bis zwoelf plus siebenhundert ausgehoben. "
    "Morgen geht es mit dem Kabelzug weiter."
)


def _engine_antwort(inhalt: dict) -> dict:
    """Antwortformat der OpenAI-kompatiblen API."""
    return {
        "id": "gen-test",
        "choices": [{"message": {"role": "assistant", "content": json.dumps(inhalt)}}],
    }


def _client_mit_antwort(inhalt: dict, aufzeichnung: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        aufzeichnung.append(request)
        return httpx.Response(200, json=_engine_antwort(inhalt))

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_nicht_genannte_geraete_bleiben_leer():
    """Kein Geraet im Transkript -> leere Liste, keine erfundenen Werte."""
    aufzeichnung: list[httpx.Request] = []
    antwort_inhalt = {
        "personal_gewerblich": 8,
        "personal_angestellt": None,
        "geraete": [],
        "leistungen": [
            {
                "beschreibung": "Rohrgraben ausgehoben",
                "station_von": "12+400",
                "station_bis": "12+700",
            }
        ],
        "ereignisse": [],
        "vorschau": "Kabelzug",
        "bemerkungen": None,
    }

    with _client_mit_antwort(antwort_inhalt, aufzeichnung) as client:
        daten = strukturiere_transkript(TRANSKRIPT_OHNE_GERAETE, client=client)

    assert daten.geraete == []
    assert daten.ereignisse == []
    assert daten.personal_gewerblich == 8
    assert daten.personal_angestellt is None
    assert daten.bemerkungen is None
    assert daten.leistungen[0].station_von == "12+400"


def test_anfrage_erzwingt_datenschutz_und_schema():
    """Jeder Aufruf traegt zdr, data_collection=deny, require_parameters und Schema."""
    aufzeichnung: list[httpx.Request] = []
    leere_antwort = TagesberichtDaten().model_dump(mode="json")

    with _client_mit_antwort(leere_antwort, aufzeichnung) as client:
        strukturiere_transkript("Nur ein kurzer Satz.", client=client)

    assert len(aufzeichnung) == 1
    anfrage = aufzeichnung[0]
    assert str(anfrage.url).endswith("/chat/completions")
    assert anfrage.headers["authorization"].startswith("Bearer ")

    nutzlast = json.loads(anfrage.content)
    assert nutzlast["model"] == config.MODEL_ECHO
    assert nutzlast["provider"] == {
        "zdr": True,
        "data_collection": "deny",
        "require_parameters": True,
    }
    assert nutzlast["response_format"]["type"] == "json_schema"
    assert nutzlast["response_format"]["json_schema"]["strict"] is True
    assert nutzlast["temperature"] == 0.0
    # Das eiserne Prinzip steht im Systemprompt.
    assert "bleibt es leer" in nutzlast["messages"][0]["content"]
    assert "Erfinde nichts" in SYSTEM_PROMPT


def test_schema_ist_strikt_gueltig():
    schema = strict_json_schema(TagesberichtDaten)

    def pruefe(knoten: dict) -> None:
        if knoten.get("type") == "object" and "properties" in knoten:
            assert knoten["additionalProperties"] is False
            assert set(knoten["required"]) == set(knoten["properties"])
        for wert in knoten.values():
            if isinstance(wert, dict):
                pruefe(wert)
            elif isinstance(wert, list):
                for eintrag in wert:
                    if isinstance(eintrag, dict):
                        pruefe(eintrag)

    pruefe(schema)
    assert "default" not in json.dumps(schema)
    # Optionale Felder bleiben ueber "null" wirklich optional.
    assert {"type": "null"} in schema["properties"]["vorschau"]["anyOf"]


def test_leeres_transkript_wird_abgelehnt():
    with pytest.raises(EngineFehler):
        strukturiere_transkript("   ")


def test_schemaverletzung_wird_zu_klarem_fehler():
    aufzeichnung: list[httpx.Request] = []
    # personal_gewerblich als Text -> passt nicht zum Schema
    with _client_mit_antwort({"personal_gewerblich": "acht"}, aufzeichnung) as client:
        with pytest.raises(EngineFehler):
            strukturiere_transkript("Acht Mann vor Ort.", client=client)


def test_ablehnung_der_engine_wird_gemeldet():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"refusal": "nein"}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EngineFehler):
            strukturiere_transkript("Kurzer Bericht.", client=client)

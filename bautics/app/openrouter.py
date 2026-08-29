"""Zugang zur Bautics Engine - technisch: OpenRouter (OpenAI-kompatibel).

Jeder Modellaufruf laeuft hierueber und damit zwingend mit:
- Zero Data Retention und ``data_collection: "deny"`` (Provider-Routing),
- ``require_parameters: true`` (kein stiller Fallback auf Provider ohne
  Structured Outputs),
- ``response_format`` mit ``json_schema`` gegen ein Pydantic-Modell.

Nach aussen wird dieser Baustein nie beim Namen genannt - im Bericht steht
"Bautics Engine". In Logs landen ausschliesslich Metadaten, keine Inhalte.
"""

import json
import logging
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from . import config
from .http_client import UpstreamFehler, anfrage_mit_retry, json_antwort

logger = logging.getLogger(__name__)

DIENST = "Bautics Engine"

# Typvariable, damit der Aufrufer den konkreten Modelltyp zurueckbekommt.
AusgabeModell = TypeVar("AusgabeModell", bound=BaseModel)

# Schluessel, die strikte JSON-Schemas nicht vertragen bzw. nicht brauchen.
_UNERWUENSCHTE_SCHLUESSEL = frozenset({"default", "title"})


class EngineFehler(RuntimeError):
    """Die Engine hat keine schemakonforme Antwort geliefert."""


def strict_json_schema(modell: type[BaseModel]) -> dict[str, Any]:
    """Pydantic-Schema in ein strikt gueltiges JSON-Schema uebersetzen.

    Strict Mode verlangt: jedes Objekt listet alle Properties unter ``required``
    und verbietet zusaetzliche Felder. Optionale Felder bleiben ueber
    ``anyOf: [..., {"type": "null"}]`` optional - genau das brauchen wir fuer
    "was der Bauleiter nicht gesagt hat, bleibt leer".
    """
    return _bereinige(modell.model_json_schema())


def _bereinige(knoten: Any) -> Any:
    if isinstance(knoten, list):
        return [_bereinige(eintrag) for eintrag in knoten]
    if not isinstance(knoten, dict):
        return knoten

    ergebnis: dict[str, Any] = {
        schluessel: _bereinige(wert)
        for schluessel, wert in knoten.items()
        if schluessel not in _UNERWUENSCHTE_SCHLUESSEL
    }
    if ergebnis.get("type") == "object" and "properties" in ergebnis:
        ergebnis["additionalProperties"] = False
        ergebnis["required"] = list(ergebnis["properties"].keys())
    return ergebnis


def _kopfzeilen() -> dict[str, str]:
    if not config.OPENROUTER_API_KEY:
        raise EngineFehler("OPENROUTER_API_KEY ist nicht gesetzt.")
    return {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Attribution gegenueber OpenRouter, taucht nur in deren Dashboard auf.
        "HTTP-Referer": config.BASE_URL,
        "X-Title": config.OPENROUTER_APP_NAME,
    }


def strukturierte_antwort(
    *,
    modell: str,
    system_prompt: str,
    benutzer_prompt: str,
    schema_name: str,
    ausgabe_modell: type[AusgabeModell],
    client: httpx.Client | None = None,
) -> AusgabeModell:
    """Ruft die Engine auf und gibt eine validierte Instanz von ``ausgabe_modell``.

    Die Antwort ist per Structured Outputs auf das Schema festgenagelt; zusaetzlich
    validieren wir sie noch einmal selbst mit Pydantic (Gueltigkeit vor Vertrauen).
    """
    nutzlast: dict[str, Any] = {
        "model": modell,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": benutzer_prompt},
        ],
        "temperature": config.LLM_TEMPERATUR,
        "max_tokens": config.LLM_MAX_TOKENS,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": strict_json_schema(ausgabe_modell),
            },
        },
        # Datenschutz-Routing: siehe config.OPENROUTER_PROVIDER_ROUTING
        "provider": dict(config.OPENROUTER_PROVIDER_ROUTING),
    }

    eigener_client = client is None
    aktiver_client = client or httpx.Client(timeout=config.HTTP_TIMEOUT_SEKUNDEN)
    try:
        antwort = anfrage_mit_retry(
            DIENST,
            lambda: aktiver_client.post(
                f"{config.OPENROUTER_BASE_URL}/chat/completions",
                headers=_kopfzeilen(),
                json=nutzlast,
            ),
        )
        daten = json_antwort(DIENST, antwort)
    finally:
        if eigener_client:
            aktiver_client.close()

    inhalt = _inhalt_extrahieren(daten)
    try:
        return ausgabe_modell.model_validate_json(inhalt)
    except ValidationError as fehler:
        # Kein Inhalt ins Log - nur die Feldpfade, an denen es scheitert.
        logger.error(
            "Engine-Antwort verletzt das Schema an: %s",
            [".".join(str(teil) for teil in f["loc"]) for f in fehler.errors()],
        )
        raise EngineFehler("Antwort der Engine passt nicht zum Schema.") from fehler
    except json.JSONDecodeError as fehler:
        raise EngineFehler("Antwort der Engine war kein gueltiges JSON.") from fehler


def _inhalt_extrahieren(daten: dict[str, Any]) -> str:
    """Holt den Textinhalt der ersten Wahlmoeglichkeit - mit klaren Fehlern."""
    if "error" in daten:
        fehlerobjekt = daten.get("error")
        hinweis = (
            fehlerobjekt.get("message", "unbekannt")
            if isinstance(fehlerobjekt, dict)
            else "unbekannt"
        )
        raise UpstreamFehler(DIENST, f"Fehlerantwort: {hinweis}")

    auswahl = daten.get("choices") or []
    if not auswahl:
        raise EngineFehler("Engine hat keine Antwort geliefert.")

    nachricht = auswahl[0].get("message") or {}
    if nachricht.get("refusal"):
        raise EngineFehler("Engine hat die Bearbeitung abgelehnt.")

    inhalt = nachricht.get("content")
    if not isinstance(inhalt, str) or not inhalt.strip():
        raise EngineFehler("Engine-Antwort war leer.")
    return inhalt

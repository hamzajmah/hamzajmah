"""Darstellungsregeln der Oberflaeche - Fachlogik bleibt aussen vor.

Grundsatz aus DESIGN.md: Ein Zustand wird nie allein ueber Farbe gezeigt.
Jede Plakette traegt deshalb Symbol *und* Text; die Farbe kommt obendrauf und
faellt im Graustufendruck ersatzlos weg, ohne dass Information verloren geht.
"""

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from .. import db
from ..report import (
    LEER_MARKIERUNG,
    formatiere_berichtsnummer,
    formatiere_station,
    formatiere_stunden,
)
from ..schemas import TagesberichtDaten


@dataclass(frozen=True)
class Zustand:
    """Wie ein Zustand angezeigt wird."""

    text: str
    symbol: str
    # Tailwind-Klassen aus dem Theme - keine Hex-Werte im Markup.
    plakette: str
    kante: str


# Ereignisarten. Behinderung und Stillstand teilen sich die kritische Farbe,
# Mehrleistung und Maengel die Pruef-Farbe - unterschieden werden sie ueber
# Symbol und Beschriftung, wie es die Barrierefreiheit verlangt.
EREIGNIS_ZUSTAENDE: dict[str, Zustand] = {
    "behinderung": Zustand("Behinderung", "▲", "bg-crit-soft text-crit", "border-l-crit"),
    "stillstand": Zustand("Stillstand", "■", "bg-crit-soft text-crit", "border-l-crit"),
    "mehrleistung": Zustand("Mehrleistung", "+", "bg-warn-soft text-warn", "border-l-warn"),
    "maengel": Zustand("Mängel", "!", "bg-warn-soft text-warn", "border-l-warn"),
    "sonstiges": Zustand(
        "Sonstiges", "•", "bg-paper text-ink-muted border border-hair", "border-l-hair-strong"
    ),
}
EREIGNIS_UNBEKANNT = Zustand(
    "Unbekannte Art", "?", "bg-paper text-ink-muted border border-hair", "border-l-hair-strong"
)

# Lebenszyklus eines Tagesberichts.
BERICHT_ZUSTAENDE: dict[str, Zustand] = {
    db.STATUS_EMPFANGEN: Zustand("In Arbeit", "◷", "bg-warn-soft text-warn", "border-l-warn"),
    db.STATUS_FERTIG: Zustand("Fertig", "✓", "bg-ok-soft text-ok", "border-l-ok"),
    db.STATUS_FEHLER: Zustand("Fehler", "✕", "bg-crit-soft text-crit", "border-l-crit"),
}
BERICHT_UNBEKANNT = Zustand(
    "Unbekannt", "?", "bg-paper text-ink-muted border border-hair", "border-l-hair-strong"
)


def ereignis_zustand(art: str) -> Zustand:
    return EREIGNIS_ZUSTAENDE.get(art, EREIGNIS_UNBEKANNT)


def bericht_zustand(status: str) -> Zustand:
    return BERICHT_ZUSTAENDE.get(status, BERICHT_UNBEKANNT)


def kurzes_datum(datum: Optional[dt.date]) -> str:
    return datum.strftime("%d.%m.%Y") if datum else LEER_MARKIERUNG


def zeitpunkt(wert: Optional[dt.datetime]) -> str:
    return wert.strftime("%d.%m.%Y, %H:%M") if wert else LEER_MARKIERUNG


def iso_zeitpunkt(wert: Optional[str]) -> str:
    """ISO-Zeitstempel aus ``wissensbank_status`` lesbar machen."""
    if not wert:
        return "noch nie"
    try:
        return zeitpunkt(dt.datetime.fromisoformat(wert))
    except ValueError:
        return wert


def berichtsnummer(bericht: db.Tagesbericht) -> str:
    """'003/2026' - dieselbe Schreibweise wie im gerenderten Bericht."""
    return formatiere_berichtsnummer(bericht.berichtsnummer, bericht.datum)


def lese_daten(bericht: db.Tagesbericht) -> Optional[TagesberichtDaten]:
    """Strukturierte Daten des Berichts, oder None.

    Aeltere oder unvollstaendige Datensaetze duerfen die Seite nicht
    zerlegen - dann wird der Block schlicht nicht angezeigt und der
    Berichtstext bleibt sichtbar.
    """
    if not bericht.daten_json:
        return None
    try:
        return TagesberichtDaten.model_validate(bericht.daten_json)
    except Exception:  # noqa: BLE001 - Anzeige darf nie an Altdaten scheitern
        return None


# Fuer die Templates: Formatierer als Jinja-Filter registrierbar.
FILTER = {
    "station": formatiere_station,
    # Dieselben Formatierer wie im gerenderten Berichtstext - die Oberflaeche
    # soll Stunden und Stationierungen nicht anders schreiben als das PDF.
    "stunden": formatiere_stunden,
    "kurzdatum": kurzes_datum,
    "zeitpunkt": zeitpunkt,
    "iso_zeitpunkt": iso_zeitpunkt,
}

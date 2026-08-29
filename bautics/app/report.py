"""Echo, Schritt 3: strukturierte Daten -> lesbarer Tagesbericht.

Der gerenderte Text ist kundensichtbar. Deshalb gilt hier besonders:
kein Modellname (nur "Bautics Engine") und nichts, was der Bauleiter nicht
gesagt hat - fehlende Angaben bleiben sichtbar leer.
"""

import datetime as dt
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel

from .schemas import Ereignis, Leistung, TagesberichtDaten

TEMPLATE_VERZEICHNIS = Path(__file__).parent / "templates"
TEMPLATE_NAME = "tagesbericht.txt.j2"

# Sichtbarer Platzhalter statt erfundener Werte.
LEER_MARKIERUNG = "– keine Angabe –"
# TODO: Wetter kommt spaeter aus der Wetter-API (Station + Datum -> Wetterdaten).
WETTER_PLATZHALTER = "– wird automatisch ergänzt –"

WOCHENTAGE = (
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
)

EREIGNIS_BEZEICHNUNGEN = {
    "behinderung": "Behinderung",
    "stillstand": "Stillstand",
    "mehrleistung": "Mehrleistung",
    "maengel": "Mängel",
    "sonstiges": "Sonstiges",
}


class BerichtMetadaten(BaseModel):
    """Alles, was nicht aus der Sprachnachricht stammt."""

    projekt: str
    datum: dt.date
    berichtsnummer: Optional[int] = None
    bauleiter: Optional[str] = None
    # Bleibt vorerst leer, bis die Wetter-API angebunden ist.
    wetter: Optional[str] = None


def formatiere_datum(datum: dt.date) -> str:
    """'Freitag, 29.08.2026' - ohne Abhaengigkeit von der System-Locale."""
    return f"{WOCHENTAGE[datum.weekday()]}, {datum.strftime('%d.%m.%Y')}"


def formatiere_berichtsnummer(nummer: Optional[int], datum: dt.date) -> str:
    if nummer is None:
        return LEER_MARKIERUNG
    return f"{nummer:03d}/{datum.year}"


def formatiere_station(objekt: Leistung | Ereignis) -> str:
    """'Station 12+400 bis 12+700', 'ab Station 12+400' oder leer."""
    von = (objekt.station_von or "").strip()
    bis = (objekt.station_bis or "").strip()
    if von and bis:
        return f"Station {von} bis {bis}"
    if von:
        return f"ab Station {von}"
    if bis:
        return f"bis Station {bis}"
    return ""


def _formatiere_stunden(wert: float) -> str:
    """2.0 -> '2', 1.5 -> '1,5' (deutsches Dezimalkomma)."""
    if float(wert).is_integer():
        return str(int(wert))
    return f"{wert:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _umgebung() -> Environment:
    umgebung = Environment(
        loader=FileSystemLoader(TEMPLATE_VERZEICHNIS),
        autoescape=False,  # reiner Text, kein HTML
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )
    umgebung.filters["station"] = formatiere_station
    umgebung.filters["stunden"] = _formatiere_stunden
    umgebung.filters["ereignisart"] = lambda art: EREIGNIS_BEZEICHNUNGEN.get(art, str(art))
    return umgebung


def rendere_tagesbericht(daten: TagesberichtDaten, meta: BerichtMetadaten) -> str:
    """Fertigen Berichtstext erzeugen."""
    vorlage = _umgebung().get_template(TEMPLATE_NAME)
    text = vorlage.render(
        daten=daten,
        meta=meta,
        datum_lang=formatiere_datum(meta.datum),
        berichtsnummer=formatiere_berichtsnummer(meta.berichtsnummer, meta.datum),
        leer=LEER_MARKIERUNG,
        wetter_platzhalter=WETTER_PLATZHALTER,
    )
    # Jinja hinterlaesst bei optionalen Bloecken gern Leerzeilenketten.
    zeilen = [zeile.rstrip() for zeile in text.splitlines()]
    entschlackt: list[str] = []
    for zeile in zeilen:
        if not zeile and entschlackt and not entschlackt[-1]:
            continue
        entschlackt.append(zeile)
    return "\n".join(entschlackt).strip() + "\n"


def kurzfassung(daten: TagesberichtDaten) -> str:
    """Einzeiler fuer die WhatsApp-Rueckmeldung an den Bauleiter."""
    teile = [f"{len(daten.leistungen)} Leistung(en)"]
    if daten.ereignisse:
        teile.append(f"{len(daten.ereignisse)} besonderes Vorkommnis/Vorkommnisse")
    if daten.geraete:
        teile.append(f"{len(daten.geraete)} Gerät(e)")
    return ", ".join(teile)

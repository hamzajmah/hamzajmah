"""Datenschemata der Berichte.

Diese Pydantic-Modelle sind zugleich das erzwungene Ausgabeformat der
KI-Strukturierung (Structured Outputs): Die API garantiert, dass die Antwort
exakt diesem Schema entspricht - kein kaputtes JSON, keine erfundenen Felder.
Eisernes Prinzip: Was der Bauleiter nicht gesagt hat, bleibt leer.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Leistung(BaseModel):
    """Eine erbrachte Leistung, moeglichst mit Stationierung (z.B. 12+400)."""

    beschreibung: str
    station_von: Optional[str] = Field(
        default=None, description="Stationierung Beginn, Format wie '12+400'"
    )
    station_bis: Optional[str] = Field(
        default=None, description="Stationierung Ende, Format wie '12+700'"
    )


class Ereignis(BaseModel):
    """Besonderes Vorkommnis - Rohstoff der spaeteren Nachtrags-Beweiskette."""

    art: Literal["behinderung", "stillstand", "mehrleistung", "maengel", "sonstiges"]
    beschreibung: str
    dauer_stunden: Optional[float] = Field(
        default=None, description="Dauer in Stunden, falls genannt"
    )
    station_von: Optional[str] = None
    station_bis: Optional[str] = None


class TagesberichtDaten(BaseModel):
    """Der strukturierte Inhalt eines Bautagesberichts.

    Wetter und Berichtsnummer kommen NICHT vom Sprachmemo - Wetter liefert
    die Wetter-API, die Nummer vergibt die Datenbank fortlaufend.
    """

    personal_gewerblich: Optional[int] = Field(
        default=None, description="Anzahl gewerbliche Mitarbeiter, falls genannt"
    )
    personal_angestellt: Optional[int] = Field(
        default=None, description="Anzahl Angestellte/Bauleitung, falls genannt"
    )
    geraete: list[str] = Field(
        default_factory=list,
        description="Eingesetzte Grossgeraete, z.B. '2 Kettenbagger', '1 Raupe'",
    )
    leistungen: list[Leistung] = Field(
        default_factory=list, description="Erbrachte Leistungen des Tages"
    )
    ereignisse: list[Ereignis] = Field(
        default_factory=list,
        description="Behinderungen, Stillstaende, Mehrleistungen - nur wenn genannt",
    )
    vorschau: Optional[str] = Field(
        default=None, description="Angekuendigte Arbeiten der naechsten Tage"
    )
    bemerkungen: Optional[str] = Field(
        default=None, description="Sonstiges, das in kein anderes Feld passt"
    )

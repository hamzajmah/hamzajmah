"""Rendering des Tagesberichts."""

import datetime as dt

from app.report import (
    LEER_MARKIERUNG,
    BerichtMetadaten,
    formatiere_berichtsnummer,
    formatiere_datum,
    rendere_tagesbericht,
)
from app.schemas import Ereignis, Leistung, TagesberichtDaten

META = BerichtMetadaten(
    projekt="SuedLink Baulos 4",
    datum=dt.date(2026, 8, 28),
    berichtsnummer=42,
    bauleiter="M. Bauer",
)


def _beispieldaten() -> TagesberichtDaten:
    return TagesberichtDaten(
        personal_gewerblich=8,
        personal_angestellt=2,
        geraete=["2 Kettenbagger", "1 Raupe"],
        leistungen=[
            Leistung(
                beschreibung="Rohrgraben ausgehoben",
                station_von="12+400",
                station_bis="12+700",
            ),
            Leistung(beschreibung="Kabelschutzrohre verlegt"),
        ],
        ereignisse=[
            Ereignis(
                art="behinderung",
                beschreibung="Wartezeit wegen fehlender Freigabe des Grundstueckseigentuemers",
                dauer_stunden=2.5,
                station_von="12+700",
            )
        ],
        vorschau="Morgen Kabelzug im selben Abschnitt",
        bemerkungen=None,
    )


def test_bericht_enthaelt_alle_angaben():
    text = rendere_tagesbericht(_beispieldaten(), META)

    assert "SuedLink Baulos 4" in text
    assert "Freitag, 28.08.2026" in text
    assert "042/2026" in text
    assert "M. Bauer" in text
    assert "2 Kettenbagger" in text
    assert "Rohrgraben ausgehoben (Station 12+400 bis 12+700)" in text
    assert "Kabelschutzrohre verlegt" in text
    assert "[Behinderung]" in text
    assert "Dauer: 2,5 h" in text
    assert "ab Station 12+700" in text
    assert "Morgen Kabelzug im selben Abschnitt" in text


def test_fehlende_angaben_bleiben_leer():
    """Leere Daten duerfen nichts erfinden - ueberall sichtbarer Platzhalter."""
    text = rendere_tagesbericht(TagesberichtDaten(), META)

    assert text.count(LEER_MARKIERUNG) >= 6
    # Wetter kommt spaeter aus der API, nicht aus der Sprachnachricht.
    assert "wird automatisch ergänzt" in text
    for verboten in ("Kettenbagger", "Raupe", "Behinderung"):
        assert verboten not in text


def test_kein_modellname_im_kundentext():
    """Nach aussen heisst es ausschliesslich 'Bautics Engine'."""
    text = rendere_tagesbericht(_beispieldaten(), META).lower()

    assert "bautics engine" in text
    for modellname in ("claude", "anthropic", "openrouter", "gpt", "whisper", "sonnet"):
        assert modellname not in text


def test_bericht_ohne_nummer_bleibt_ehrlich():
    meta = META.model_copy(update={"berichtsnummer": None, "bauleiter": None})
    text = rendere_tagesbericht(TagesberichtDaten(), meta)

    assert f"Bericht-Nr.:   {LEER_MARKIERUNG}" in text


def test_hilfsformatierungen():
    assert formatiere_datum(dt.date(2026, 8, 28)).startswith("Freitag")
    assert formatiere_berichtsnummer(7, dt.date(2026, 1, 5)) == "007/2026"
    assert formatiere_berichtsnummer(None, dt.date(2026, 1, 5)) == LEER_MARKIERUNG

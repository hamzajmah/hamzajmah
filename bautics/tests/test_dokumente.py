"""Einlesen der Projektunterlagen - PDF, DOCX, Text."""

import datetime as dt

import pytest
from conftest import baue_pdf

from app import dokumente
from app.dokumente import KeinTextFehler, lese_dokument, saeubere_text


def test_pdf_seiten_werden_einzeln_gelesen(wissensbank):
    ordner = wissensbank / "SuedLink Baulos 4"
    ordner.mkdir()
    pfad = ordner / "LV_Los4.pdf"
    pfad.write_bytes(
        baue_pdf(
            [
                "1 Allgemeines\nDer Auftragnehmer stellt die Baustelle ein.",
                "2 Rohrgraben\nStation 12+400 bis 12+700 ausgehoben.",
            ]
        )
    )

    inhalt = lese_dokument(pfad, wissensbank)

    assert inhalt.dateiformat == "pdf"
    assert inhalt.seiten_pflicht is True
    assert [seite.nummer for seite in inhalt.seiten] == [1, 2]
    assert "Rohrgraben" in inhalt.seiten[1].text
    # Provenienz aus Pfad und Dateiname
    assert inhalt.projekt == "SuedLink Baulos 4"
    assert inhalt.dokumenttyp == "Leistungsverzeichnis"
    assert isinstance(inhalt.geaendert_am, dt.datetime)
    assert len(inhalt.datei_hash) == 64


def test_pdf_ohne_textebene_wird_gemeldet(wissensbank):
    """Gescanntes PDF: lieber ein klarer Fehler als ein leerer Index."""
    pfad = wissensbank / "Scan.pdf"
    pfad.write_bytes(baue_pdf([""]))

    with pytest.raises(KeinTextFehler):
        lese_dokument(pfad, wissensbank)


def test_docx_mit_ueberschrift_und_tabelle(wissensbank):
    docx = pytest.importorskip("docx")
    pfad = wissensbank / "Bauvertrag.docx"
    dokument = docx.Document()
    dokument.add_heading("2 Vertragsfristen", level=1)
    dokument.add_paragraph(
        "Die Ausfuehrungsfrist beginnt mit der Uebergabe des Bauablaufplans."
    )
    tabelle = dokument.add_table(rows=2, cols=2)
    tabelle.cell(0, 0).text = "Position"
    tabelle.cell(0, 1).text = "Frist"
    tabelle.cell(1, 0).text = "03.02.040"
    tabelle.cell(1, 1).text = "12 Wochen"
    dokument.save(str(pfad))

    inhalt = lese_dokument(pfad, wissensbank)

    assert inhalt.dateiformat == "docx"
    # DOCX kennt keine verlaessliche Seitenzahl - Fundstelle traegt der Abschnitt.
    assert inhalt.seiten_pflicht is False
    assert inhalt.seiten[0].nummer is None
    text = inhalt.seiten[0].text
    assert "2 Vertragsfristen" in text
    assert "03.02.040 | 12 Wochen" in text
    assert inhalt.dokumenttyp == "Vertrag"


def test_markdown_und_txt_werden_gelesen(wissensbank):
    (wissensbank / "Notizen.md").write_text("# Baubesprechung\n\nInhalt.", encoding="utf-8")
    (wissensbank / "Hinweis.txt").write_text("Nur ein Hinweis.", encoding="utf-8")

    gefunden = dokumente.finde_dokumente(wissensbank)

    assert {pfad.name for pfad in gefunden} == {"Notizen.md", "Hinweis.txt"}
    assert lese_dokument(wissensbank / "Notizen.md", wissensbank).seiten[0].nummer is None


def test_office_sperrdateien_werden_uebergangen(wissensbank):
    (wissensbank / "~$Bauvertrag.docx").write_bytes(b"unsinn")
    (wissensbank / "Bericht.txt").write_text("Text.", encoding="utf-8")

    assert [pfad.name for pfad in dokumente.finde_dokumente(wissensbank)] == ["Bericht.txt"]


def test_projekt_kommt_aus_dem_unterordner(wissensbank):
    ordner = wissensbank / "Los 7"
    ordner.mkdir()
    (ordner / "Protokoll.txt").write_text("Text.", encoding="utf-8")

    assert dokumente.projekt_aus_pfad(ordner / "Protokoll.txt", wissensbank) == "Los 7"
    # Direkt im Wurzelordner: Standardprojekt aus der Konfiguration.
    assert dokumente.projekt_aus_pfad(wissensbank / "x.txt", wissensbank) != "Los 7"


def test_dokumenttyp_aus_dateiname():
    assert dokumente.dokumenttyp_aus_name("Nachtrag_N-012.pdf") == "Nachtrag"
    assert dokumente.dokumenttyp_aus_name("Bautagebuch_KW32.pdf") == "Bautagesbericht"
    assert dokumente.dokumenttyp_aus_name("Irgendwas.pdf") == dokumente.DOKUMENTTYP_STANDARD


def test_textbereinigung_setzt_getrennte_woerter_zusammen():
    """Sonst steht 'Kabel-schutzrohr' im Zitat und der Beleg wirkt falsch."""
    roh = "Das Kabel-\nschutzrohr wird verlegt.\n\n\n\nNaechster Absatz.   Ende"

    text = saeubere_text(roh)

    assert "Kabelschutzrohr" in text
    assert "\n\n\n" not in text
    assert "   " not in text

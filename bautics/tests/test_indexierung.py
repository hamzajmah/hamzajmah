"""Indexierung der Wissensbank - Provenienz und Dublettenfreiheit."""

from conftest import baue_pdf, pseudo_vektoren
from sqlalchemy import func, select

from app import db, mind
from app.mind import MindDienste, indexiere_wissensbank

SEITE_EINS = (
    "1 Allgemeine Vorbemerkungen\n"
    "Der Auftragnehmer haelt die Baustelle in verkehrssicherem Zustand.\n"
    "Die Wasserhaltung ist als Nebenleistung zu erbringen."
)
SEITE_ZWEI = (
    "03.02.040 Rohrgraben herstellen\n"
    "Rohrgraben herstellen, Aushub lagenweise verfuellen und verdichten.\n"
    "Abrechnung nach laufendem Meter."
)


def _dienste() -> MindDienste:
    """Keine echten Aufrufe - Vektoren kommen aus dem Testersatz."""
    return MindDienste(vektoren=pseudo_vektoren, antworte=_kein_modellaufruf)


def _kein_modellaufruf(system_prompt: str, benutzer_prompt: str):
    raise AssertionError("Beim Indexieren wird kein Sprachmodell befragt.")


def _lv(wissensbank, seiten=None):
    ordner = wissensbank / "SuedLink Baulos 4"
    ordner.mkdir(exist_ok=True)
    pfad = ordner / "LV_Los4.pdf"
    pfad.write_bytes(baue_pdf(seiten or [SEITE_EINS, SEITE_ZWEI]))
    return pfad


def _zaehle(modell) -> int:
    with db.sitzung() as sitzung:
        return sitzung.scalar(select(func.count(modell.id))) or 0


def test_indexlauf_legt_dokument_und_chunks_an(datenbank, wissensbank):
    _lv(wissensbank)

    bericht = indexiere_wissensbank(wissensbank, _dienste())

    assert bericht.geprueft == 1
    assert bericht.neu == 1
    assert bericht.chunks > 0
    assert bericht.fehler == []
    assert _zaehle(db.WissensDokument) == 1
    assert _zaehle(db.WissensChunk) == bericht.chunks


def test_jeder_gespeicherte_chunk_hat_provenienz(datenbank, wissensbank):
    _lv(wissensbank)

    indexiere_wissensbank(wissensbank, _dienste())

    with db.sitzung() as sitzung:
        chunks = sitzung.scalars(select(db.WissensChunk)).all()
        assert chunks
        for chunk in chunks:
            assert chunk.dateiname == "LV_Los4.pdf"
            # PDF: Seitenzahl ist Pflicht, sonst gibt es keine Fundstelle.
            assert chunk.seite in (1, 2)
            assert chunk.projekt == "SuedLink Baulos 4"
            assert chunk.dokumenttyp == "Leistungsverzeichnis"
            assert chunk.embedding and len(chunk.embedding) == 64
            assert chunk.text_klein == chunk.text.lower()


def test_zweiter_lauf_erzeugt_keine_dubletten(datenbank, wissensbank):
    """Wiederholtes Einlesen derselben Datei darf nichts verdoppeln."""
    _lv(wissensbank)
    erster = indexiere_wissensbank(wissensbank, _dienste())
    chunks_nach_erstem = _zaehle(db.WissensChunk)

    zweiter = indexiere_wissensbank(wissensbank, _dienste())

    assert zweiter.unveraendert == 1
    assert zweiter.neu == 0
    assert erster.chunks == chunks_nach_erstem
    assert _zaehle(db.WissensChunk) == chunks_nach_erstem
    assert _zaehle(db.WissensDokument) == 1


def test_geaenderte_datei_ersetzt_ihre_alten_chunks(datenbank, wissensbank):
    pfad = _lv(wissensbank)
    indexiere_wissensbank(wissensbank, _dienste())

    # Dieselbe Datei, neuer Inhalt.
    pfad.write_bytes(
        baue_pdf(["9 Neue Vorbemerkungen\nDer Bauablaufplan wurde vollstaendig ersetzt."])
    )
    bericht = indexiere_wissensbank(wissensbank, _dienste())

    assert bericht.aktualisiert == 1
    assert _zaehle(db.WissensDokument) == 1
    with db.sitzung() as sitzung:
        texte = sitzung.scalars(select(db.WissensChunk.text)).all()
    assert any("Bauablaufplan" in text for text in texte)
    # Der alte Stand ist weg - sonst wuerde Mind veraltete Fundstellen zitieren.
    assert not any("Wasserhaltung" in text for text in texte)


def test_gescanntes_pdf_wird_als_fehler_vermerkt(datenbank, wissensbank):
    """Kein Text, kein Index - aber sichtbar, nicht stillschweigend."""
    (wissensbank / "Scan.pdf").write_bytes(baue_pdf([""]))

    bericht = indexiere_wissensbank(wissensbank, _dienste())

    assert bericht.fehler and "Scan.pdf" in bericht.fehler[0]
    assert _zaehle(db.WissensChunk) == 0
    with db.sitzung() as sitzung:
        dokument = db.finde_dokument_nach_pfad(sitzung, "Scan.pdf")
    assert dokument is not None
    assert dokument.status == db.DOKUMENT_FEHLER
    assert dokument.chunk_anzahl == 0


def test_kaputte_datei_bricht_den_lauf_nicht_ab(datenbank, wissensbank):
    (wissensbank / "Kaputt.pdf").write_bytes(b"kein PDF")
    _lv(wissensbank)

    bericht = indexiere_wissensbank(wissensbank, _dienste())

    assert bericht.geprueft == 2
    assert bericht.neu == 1
    assert len(bericht.fehler) == 1


def test_status_meldet_kennzahlen(datenbank, wissensbank):
    _lv(wissensbank)
    indexiere_wissensbank(wissensbank, _dienste())

    status = mind.wissensbank_status()

    assert status["dokumente"] == 1
    assert status["chunks"] > 0
    assert status["projekte"] == ["SuedLink Baulos 4"]
    assert status["zuletzt_indexiert"]


def test_docx_wird_ohne_seitenzahl_indexiert(datenbank, wissensbank):
    import pytest

    docx = pytest.importorskip("docx")
    dokument = docx.Document()
    dokument.add_heading("2 Vertragsfristen", level=1)
    dokument.add_paragraph(
        "Die Ausfuehrungsfrist beginnt mit der Uebergabe des Bauablaufplans "
        "und endet mit der foermlichen Abnahme der Leistung."
    )
    dokument.save(str(wissensbank / "Bauvertrag.docx"))

    bericht = indexiere_wissensbank(wissensbank, _dienste())

    assert bericht.neu == 1
    with db.sitzung() as sitzung:
        chunks = sitzung.scalars(select(db.WissensChunk)).all()
    assert chunks
    assert all(chunk.seite is None for chunk in chunks)
    assert any(chunk.abschnitt == "2 Vertragsfristen" for chunk in chunks)

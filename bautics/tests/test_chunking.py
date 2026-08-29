"""Zerlegung in Chunks: Provenienz, Seitenzahlen, natuerliche Grenzen."""

import datetime as dt

import pytest

from app import chunking
from app.chunking import Chunk, ProvenienzFehler, ist_ueberschrift, zerlege_dokument
from app.dokumente import DokumentInhalt, Seite

SEITE_EINS = """\
1 Allgemeine Vorbemerkungen

Der Auftragnehmer haelt die Baustelle in verkehrssicherem Zustand. Zufahrten
sind freizuhalten.

Die Wasserhaltung ist Nebenleistung nach DIN 18300.

2 Rohrgraben

Der Rohrgraben wird nach ZTV-A hergestellt und lagenweise verfuellt.
"""

SEITE_ZWEI = """\
3 Kabelzug

Der Kabelzug erfolgt nach Freigabe durch die Bauleitung.
"""


def _inhalt(seiten: list[Seite], *, dateiformat: str = "pdf") -> DokumentInhalt:
    return DokumentInhalt(
        pfad=__import__("pathlib").Path("/tmp/LV_Los4.pdf"),
        dateiname="LV_Los4.pdf",
        dateiformat=dateiformat,
        dokumenttyp="Leistungsverzeichnis",
        projekt="SuedLink Baulos 4",
        geaendert_am=dt.datetime(2026, 8, 1),
        datei_hash="a" * 64,
        seiten=seiten,
    )


def test_jeder_chunk_traegt_seine_provenienz():
    """Ohne Datei und Seite waere ein Chunk spaeter nicht zitierfaehig."""
    chunks = zerlege_dokument(
        _inhalt([Seite(1, SEITE_EINS), Seite(2, SEITE_ZWEI)])
    )

    assert chunks
    for chunk in chunks:
        assert chunk.dateiname == "LV_Los4.pdf"
        assert chunk.seite in (1, 2)
        assert chunk.text.strip()
    # Positionen laufen fortlaufend durch das ganze Dokument.
    assert [chunk.position for chunk in chunks] == list(range(len(chunks)))


def test_seitenzahlen_bleiben_der_seite_treu():
    chunks = zerlege_dokument(_inhalt([Seite(1, SEITE_EINS), Seite(2, SEITE_ZWEI)]))

    seite_zwei = [chunk for chunk in chunks if chunk.seite == 2]
    assert seite_zwei
    for chunk in seite_zwei:
        # Kein Chunk darf Text von Seite 1 mitschleppen - die Seitenzahl in der
        # Fundstelle waere sonst falsch.
        assert "Vorbemerkungen" not in chunk.text
        assert "Kabelzug" in chunk.text


def test_abschnitte_werden_erkannt_und_nicht_vermischt():
    chunks = zerlege_dokument(_inhalt([Seite(1, SEITE_EINS)]))

    abschnitte = [chunk.abschnitt for chunk in chunks]
    assert "1 Allgemeine Vorbemerkungen" in abschnitte
    assert "2 Rohrgraben" in abschnitte
    # Zwei Abschnitte auf einer Seite = mindestens zwei Chunks.
    rohrgraben = [chunk for chunk in chunks if chunk.abschnitt == "2 Rohrgraben"]
    assert len(rohrgraben) == 1
    assert "Vorbemerkungen" not in rohrgraben[0].text
    # Die Ueberschrift steht im Text, damit die Suche den Abschnitt mitsieht.
    assert rohrgraben[0].text.startswith("2 Rohrgraben")


def test_absaetze_bleiben_zusammen():
    """Es wird nicht stur nach N Zeichen geschnitten."""
    absatz = (
        "Der Auftragnehmer haelt die Baustelle in verkehrssicherem Zustand. "
        "Zufahrten sind freizuhalten."
    )
    chunks = zerlege_dokument(
        _inhalt([Seite(1, f"1 Allgemeines\n\n{absatz}\n\nZweiter Absatz zum Thema.")]),
        max_zeichen=400,
    )

    volltext = " ".join(chunk.text for chunk in chunks)
    assert absatz in volltext


def test_langer_absatz_wird_mit_ueberlappung_geteilt():
    satz = "Die Wasserhaltung ist als Nebenleistung vom Auftragnehmer zu erbringen. "
    langer_absatz = satz * 30
    chunks = zerlege_dokument(
        _inhalt([Seite(1, f"4 Wasserhaltung\n\n{langer_absatz}")]),
        max_zeichen=500,
        ueberlappung=150,
    )

    assert len(chunks) > 1
    for chunk in chunks:
        # Ueberschrift kommt zur Laenge hinzu, deshalb etwas Luft.
        assert len(chunk.text) <= 500 + len("4 Wasserhaltung") + 2
        assert chunk.seite == 1
        assert chunk.abschnitt == "4 Wasserhaltung"
    # Ueberlappung: das Ende des ersten Teils taucht im zweiten wieder auf.
    ende_erster = chunks[0].text[-80:].strip()
    assert ende_erster and ende_erster in chunks[1].text


def test_ueberlange_zeile_ohne_satzzeichen_wird_hart_geteilt():
    zeile = "0" * 3000
    chunks = zerlege_dokument(_inhalt([Seite(1, zeile)]), max_zeichen=500, ueberlappung=100)

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 500 for chunk in chunks)


def test_ueberschriften_erkennung():
    assert ist_ueberschrift("03.02.040 Rohrgraben herstellen")
    assert ist_ueberschrift("## Baubesprechung")
    assert ist_ueberschrift("§ 6 Behinderung")
    assert ist_ueberschrift("ALLGEMEINE VORBEMERKUNGEN")
    assert ist_ueberschrift("Anlage 2 Bauzeitenplan")
    # Fliesstext ist keine Ueberschrift.
    assert not ist_ueberschrift("Der Rohrgraben wird lagenweise verfuellt.")
    assert not ist_ueberschrift("Folgende Leistungen sind enthalten:")
    assert not ist_ueberschrift(
        "Der Auftragnehmer haelt die Baustelle in verkehrssicherem Zustand und "
        "sorgt fuer freie Zufahrten zu jeder Zeit"
    )


def test_docx_ohne_seitenzahl_ist_zulaessig():
    chunks = zerlege_dokument(
        _inhalt([Seite(None, SEITE_EINS)], dateiformat="docx")
    )

    assert chunks
    assert all(chunk.seite is None for chunk in chunks)
    assert any(chunk.abschnitt for chunk in chunks)


def test_pdf_chunk_ohne_seitenzahl_wird_abgelehnt():
    """Der Torwaechter vor der Datenbank: keine Fundstelle ohne Seite."""
    chunk = Chunk(text="Irgendein Text.", dateiname="LV.pdf", seite=None, abschnitt=None, position=0)

    with pytest.raises(ProvenienzFehler):
        chunking.pruefe_provenienz([chunk], seiten_pflicht=True)


def test_chunk_ohne_dateiname_wird_abgelehnt():
    chunk = Chunk(text="Text.", dateiname="  ", seite=1, abschnitt=None, position=0)

    with pytest.raises(ProvenienzFehler):
        chunking.pruefe_provenienz([chunk], seiten_pflicht=True)

"""Hybrid-Suche: Bedeutungssuche plus exakte Textsuche.

Der wichtigste Fall hier ist die Positionsnummer: Genau dafuer gibt es die
zweite Suchart neben den Vektoren.
"""

from conftest import lege_chunks_an, pseudo_vektor

from app import config, db, suche
from app.suche import (
    Treffer,
    fusioniere,
    hybrid_suche,
    relevante_treffer,
    volltext_treffer,
)

POSITION_TEXT = (
    "03.02.040 Rohrgraben herstellen\n\n"
    "Rohrgraben herstellen, Aushub lagenweise verfuellen und verdichten. "
    "Abrechnung nach laufendem Meter."
)

ABLENKUNGEN = [
    "Die Wasserhaltung ist als Nebenleistung vom Auftragnehmer zu erbringen.",
    "Der Kabelzug erfolgt nach schriftlicher Freigabe durch die Bauleitung.",
    "Die Rekultivierung der Flaechen wird nach Abschluss der Arbeiten geprueft.",
    "Behinderungen sind unverzueglich schriftlich anzuzeigen und zu begruenden.",
]


def _bestand():
    lege_chunks_an("LV_Los4.pdf", [POSITION_TEXT] + ABLENKUNGEN)


def test_exakte_positionsnummer_wird_gefunden(datenbank):
    """Der Grund fuer die Volltextsuche: 03.02.040 ist keine Bedeutung."""
    _bestand()
    frage = "Was ist unter Position 03.02.040 vereinbart?"

    with db.sitzung() as sitzung:
        volltext = volltext_treffer(sitzung, frage)
        vektor = suche.vektor_treffer(sitzung, pseudo_vektor(frage))
        gemischt = hybrid_suche(sitzung, frage, pseudo_vektor(frage))

    # Die Textsuche trifft die Position auf Anhieb.
    assert volltext[0].text == POSITION_TEXT
    assert volltext[0].exakter_fund is True
    # Die Bedeutungssuche allein findet sie nicht - die Nummer traegt keine
    # Bedeutung, deshalb steht der richtige Chunk dort nicht vorne.
    assert vektor[0].text != POSITION_TEXT
    # Zusammengefuehrt steht sie wieder oben.
    assert gemischt[0].text == POSITION_TEXT


def test_positionstreffer_ueberlebt_die_relevanzschwelle(datenbank):
    """Ein exakter Fund zaehlt, auch wenn die Aehnlichkeit niedrig ist."""
    _bestand()
    frage = "Position 03.02.040"

    with db.sitzung() as sitzung:
        belege = relevante_treffer(
            hybrid_suche(sitzung, frage, pseudo_vektor(frage)), min_aehnlichkeit=0.99
        )

    assert [beleg.text for beleg in belege] == [POSITION_TEXT]


def test_stationierung_wird_als_harter_begriff_erkannt():
    assert suche.harte_begriffe("Was passierte bei Station 12+400?") == ["12+400"]
    assert suche.harte_begriffe("Regelung nach § 6 VOB/B")[0] == "§ 6"
    assert "03.02.040" in suche.harte_begriffe("Position 03.02.040 pruefen")


def test_bedeutungssuche_findet_andere_worte(datenbank):
    """Die Vektorsuche traegt das, was die Textsuche nicht kann."""
    _bestand()
    # Wortgleich zur Ablenkung Nr. 1, aber ohne Positionsnummer.
    frage = "Wer erbringt die Wasserhaltung als Nebenleistung?"

    with db.sitzung() as sitzung:
        treffer = suche.vektor_treffer(sitzung, pseudo_vektor(frage))

    assert treffer[0].text == ABLENKUNGEN[0]


def test_suche_laesst_sich_auf_ein_baulos_eingrenzen(datenbank):
    lege_chunks_an("LV_Los4.pdf", [POSITION_TEXT], projekt="Los 4")
    lege_chunks_an("LV_Los7.pdf", [POSITION_TEXT], projekt="Los 7")
    frage = "Position 03.02.040"

    with db.sitzung() as sitzung:
        alle = volltext_treffer(sitzung, frage)
        nur_los7 = volltext_treffer(sitzung, frage, projekt="Los 7")

    assert len(alle) == 2
    assert [treffer.dateiname for treffer in nur_los7] == ["LV_Los7.pdf"]


def test_sonderzeichen_im_suchbegriff_sprengen_die_abfrage_nicht(datenbank):
    lege_chunks_an("LV_Los4.pdf", ["Ein Text mit 100% Verdichtung und einem_Unterstrich."])

    with db.sitzung() as sitzung:
        treffer = volltext_treffer(sitzung, "Wie viel Prozent 100% Verdichtung?")

    assert treffer  # kein SQL-Fehler, und der Prozentwert trifft
    assert "100%" in treffer[0].text


def test_grossschreibung_und_umlaute_treffen(datenbank):
    """SQLite kennt kein Unicode-lower - deshalb die kleingeschriebene Spalte."""
    lege_chunks_an("Protokoll.pdf", ["Die Übergabe der Flächen erfolgt am Montag."])

    with db.sitzung() as sitzung:
        treffer = volltext_treffer(sitzung, "Wann ist die übergabe der flächen?")

    assert treffer


def _treffer(chunk_id: int, text: str = "x") -> Treffer:
    return Treffer(
        chunk_id=chunk_id,
        dateiname="a.pdf",
        seite=1,
        abschnitt=None,
        projekt="Los 4",
        dokumenttyp="Dokument",
        text=text,
    )


def test_rrf_bevorzugt_treffer_aus_beiden_verfahren():
    aus_vektoren = [_treffer(1), _treffer(2), _treffer(3)]
    aus_volltext = [_treffer(3), _treffer(4)]

    ergebnis = fusioniere([aus_vektoren, aus_volltext], k=1)

    # Chunk 3 steht in beiden Listen und gewinnt trotz schlechterer Einzelraenge.
    assert ergebnis[0].chunk_id == 3
    assert {treffer.chunk_id for treffer in ergebnis} == {1, 2, 3, 4}


def test_relevanzschwelle_sortiert_fernes_aus():
    nah = Treffer(1, "a.pdf", 1, None, "Los 4", "Dokument", "x", aehnlichkeit=0.9)
    fern = Treffer(2, "a.pdf", 2, None, "Los 4", "Dokument", "y", aehnlichkeit=0.05)

    assert relevante_treffer([nah, fern], min_aehnlichkeit=0.3) == [nah]
    assert relevante_treffer([fern], min_aehnlichkeit=0.3) == []


def test_kosinus_rechnet_richtig():
    assert suche.kosinus([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert suche.kosinus([1.0, 0.0], [0.0, 1.0]) == 0.0
    # Unterschiedliche Laengen liefern 0 statt eines Absturzes.
    assert suche.kosinus([1.0], [1.0, 0.0]) == 0.0


def test_ohne_vektor_bleibt_die_volltextsuche(datenbank):
    """Faellt der Embedding-Dienst aus, muss die Suche trotzdem etwas liefern."""
    _bestand()

    with db.sitzung() as sitzung:
        treffer = hybrid_suche(sitzung, "Position 03.02.040", None)

    assert treffer and treffer[0].text == POSITION_TEXT


def test_pgvector_wird_auf_sqlite_nicht_verwendet(datenbank):
    """Der Python-Fallback ist der Entwicklungsweg, pgvector der Produktionsweg."""
    with db.sitzung() as sitzung:
        assert suche.pgvector_verfuegbar(sitzung) is False


def test_konfiguration_liefert_das_embedding_modell():
    # Modellnamen stehen ausschliesslich in der Config, nie im Feature-Code.
    assert config.MODEL_EMBEDDING
    assert config.MODEL_MIND
    quelltext = (db.__file__, suche.__file__)
    for pfad in quelltext:
        with open(pfad, encoding="utf-8") as datei:
            assert "anthropic/" not in datei.read()

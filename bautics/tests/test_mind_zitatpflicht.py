"""Die Zitatpflicht - der Kern von Mind.

Geprueft wird hier nicht, ob das Modell brav ist, sondern ob der Code eine
unbelegte Antwort zuverlaessig abfaengt. Ein gemocktes Modell liefert genau
die Antworten, die im Betrieb Schaden anrichten wuerden.
"""

import pytest
from conftest import lege_chunks_an, pseudo_vektoren
from sqlalchemy import select

from app import config, db, mind
from app.mind import ANTWORT_NICHTS_GEFUNDEN, MindDienste, beantworte_frage
from app.openrouter import EngineFehler
from app.schemas import Fundstelle, MindAntwort

WASSERHALTUNG = (
    "4 Wasserhaltung\n\n"
    "Die Wasserhaltung ist als Nebenleistung vom Auftragnehmer zu erbringen "
    "und wird nicht gesondert verguetet."
)
BEHINDERUNG = (
    "6 Behinderung\n\n"
    "Behinderungen sind dem Auftraggeber unverzueglich schriftlich anzuzeigen."
)

FRAGE = "Wer traegt die Wasserhaltung?"
BELEG_ZITAT = (
    "Die Wasserhaltung ist als Nebenleistung vom Auftragnehmer zu erbringen"
)


@pytest.fixture
def bestand(datenbank):
    """Zwei zitierfaehige Chunks mit vollstaendiger Provenienz."""
    return lege_chunks_an(
        "LV_Los4.pdf",
        [WASSERHALTUNG, BEHINDERUNG],
        seiten=[4, 6],
        abschnitte=["4 Wasserhaltung", "6 Behinderung"],
    )


@pytest.fixture
def ohne_schwelle(monkeypatch):
    """Relevanzschwelle aus dem Weg raeumen - hier geht es um die Zitatpflicht."""
    monkeypatch.setattr(config, "MIND_MIN_AEHNLICHKEIT", 0.0)


class Modell:
    """Attrappe der Engine - liefert eine vorgegebene Antwort und zaehlt mit."""

    def __init__(self, antwort: MindAntwort | Exception) -> None:
        self.antwort = antwort
        self.aufrufe: list[str] = []

    def __call__(self, system_prompt: str, benutzer_prompt: str) -> MindAntwort:
        self.aufrufe.append(benutzer_prompt)
        if isinstance(self.antwort, Exception):
            raise self.antwort
        return self.antwort


def _dienste(modell: Modell) -> MindDienste:
    return MindDienste(vektoren=pseudo_vektoren, antworte=modell)


def _letzte_anfrage() -> db.MindAnfrage:
    with db.sitzung() as sitzung:
        return sitzung.scalars(
            select(db.MindAnfrage).order_by(db.MindAnfrage.id.desc())
        ).first()


# --- Der gute Fall ---------------------------------------------------------


def test_belegte_antwort_wird_ausgeliefert(bestand, ohne_schwelle):
    modell = Modell(
        MindAntwort(
            gefunden=True,
            antwort="Die Wasserhaltung ist Nebenleistung des Auftragnehmers.",
            fundstellen=[
                Fundstelle(datei="LV_Los4.pdf", seite=4, abschnitt=None, zitat=BELEG_ZITAT)
            ],
        )
    )

    ergebnis = beantworte_frage(FRAGE, dienste=_dienste(modell))

    assert ergebnis.gefunden is True
    assert ergebnis.antwort == "Die Wasserhaltung ist Nebenleistung des Auftragnehmers."
    assert len(ergebnis.fundstellen) == 1
    fundstelle = ergebnis.fundstellen[0]
    # Die ausgelieferte Fundstelle stammt aus der Datenbank, nicht aus der
    # Behauptung des Modells - deshalb steht hier auch der Abschnitt.
    assert fundstelle.datei == "LV_Los4.pdf"
    assert fundstelle.seite == 4
    assert fundstelle.abschnitt == "4 Wasserhaltung"
    assert fundstelle.zitat == BELEG_ZITAT


def test_fundstelle_wird_aus_der_datenbank_ergaenzt(bestand, ohne_schwelle):
    """Nennt das Modell keine Seite, ergaenzt sie der Code aus dem Chunk."""
    modell = Modell(
        MindAntwort(
            gefunden=True,
            antwort="Nebenleistung des Auftragnehmers.",
            fundstellen=[Fundstelle(datei="LV_Los4.pdf", zitat=BELEG_ZITAT)],
        )
    )

    ergebnis = beantworte_frage(FRAGE, dienste=_dienste(modell))

    assert ergebnis.gefunden is True
    assert ergebnis.fundstellen[0].seite == 4


def test_zitat_mit_zeilenumbruechen_und_anfuehrungszeichen_gilt(bestand, ohne_schwelle):
    """Leerraum und Typografie duerfen einen echten Beleg nicht scheitern lassen."""
    modell = Modell(
        MindAntwort(
            gefunden=True,
            antwort="Nebenleistung des Auftragnehmers.",
            fundstellen=[
                Fundstelle(
                    datei="LV_Los4.pdf",
                    seite=4,
                    zitat="Die Wasserhaltung ist als Nebenleistung\n  vom Auftragnehmer   zu erbringen",
                )
            ],
        )
    )

    ergebnis = beantworte_frage(FRAGE, dienste=_dienste(modell))

    assert ergebnis.gefunden is True
    # Ausgeliefert wird der Wortlaut der Unterlage, nicht die Abschrift des
    # Modells - im Nachtragsstreit zaehlt das Dokument.
    assert ergebnis.fundstellen[0].zitat == BELEG_ZITAT


def test_zitat_kommt_woertlich_aus_dem_dokument(bestand, ohne_schwelle):
    """Auch bei anderer Schreibweise steht am Ende der Dokumenttext."""
    modell = Modell(
        MindAntwort(
            gefunden=True,
            antwort="Nebenleistung des Auftragnehmers.",
            fundstellen=[
                Fundstelle(
                    datei="LV_Los4.pdf",
                    seite=4,
                    zitat="DIE WASSERHALTUNG IST ALS NEBENLEISTUNG VOM AUFTRAGNEHMER",
                )
            ],
        )
    )

    ergebnis = beantworte_frage(FRAGE, dienste=_dienste(modell))

    assert (
        ergebnis.fundstellen[0].zitat
        == "Die Wasserhaltung ist als Nebenleistung vom Auftragnehmer"
    )


# --- Die Faelle, um die es geht --------------------------------------------


def test_antwort_ohne_fundstelle_wird_verworfen(bestand, ohne_schwelle):
    """Der wichtigste Test: plausible Antwort, kein Beleg - nichts geht raus."""
    modell = Modell(
        MindAntwort(
            gefunden=True,
            antwort="Die Wasserhaltung traegt selbstverstaendlich der Auftragnehmer.",
            fundstellen=[],
        )
    )

    ergebnis = beantworte_frage(FRAGE, dienste=_dienste(modell))

    assert ergebnis.gefunden is False
    assert ergebnis.antwort == ANTWORT_NICHTS_GEFUNDEN
    assert ergebnis.fundstellen == []
    assert ergebnis.hinweis == mind.GRUND_OHNE_FUNDSTELLE
    # Der Text des Modells taucht nirgends in der Antwort auf.
    assert "selbstverstaendlich" not in ergebnis.antwort


def test_erfundene_fundstelle_wird_verworfen(bestand, ohne_schwelle):
    """Datei, die es im Bestand gar nicht gibt, samt erfundenem Zitat."""
    modell = Modell(
        MindAntwort(
            gefunden=True,
            antwort="Die Wasserhaltung wird gesondert verguetet.",
            fundstellen=[
                Fundstelle(
                    datei="Bauvertrag_Anlage7.pdf",
                    seite=12,
                    zitat="Die Wasserhaltung wird nach Aufwand gesondert verguetet.",
                )
            ],
        )
    )

    ergebnis = beantworte_frage(FRAGE, dienste=_dienste(modell))

    assert ergebnis.gefunden is False
    assert ergebnis.antwort == ANTWORT_NICHTS_GEFUNDEN
    assert ergebnis.hinweis == mind.GRUND_ZITAT_NICHT_GEFUNDEN


def test_umformuliertes_zitat_wird_verworfen(bestand, ohne_schwelle):
    """Sinngemaess ist kein Beleg - das Zitat muss woertlich dastehen."""
    modell = Modell(
        MindAntwort(
            gefunden=True,
            antwort="Nebenleistung des Auftragnehmers.",
            fundstellen=[
                Fundstelle(
                    datei="LV_Los4.pdf",
                    seite=4,
                    zitat="Die Wasserhaltung gehoert zu den Nebenleistungen des AN.",
                )
            ],
        )
    )

    ergebnis = beantworte_frage(FRAGE, dienste=_dienste(modell))

    assert ergebnis.gefunden is False
    assert ergebnis.hinweis == mind.GRUND_ZITAT_NICHT_GEFUNDEN


def test_richtiges_zitat_aber_falsche_datei_wird_verworfen(bestand, ohne_schwelle):
    modell = Modell(
        MindAntwort(
            gefunden=True,
            antwort="Nebenleistung des Auftragnehmers.",
            fundstellen=[
                Fundstelle(datei="Bauvertrag.pdf", seite=4, zitat=BELEG_ZITAT)
            ],
        )
    )

    ergebnis = beantworte_frage(FRAGE, dienste=_dienste(modell))

    assert ergebnis.gefunden is False
    assert ergebnis.hinweis == mind.GRUND_FALSCHE_QUELLE


def test_richtiges_zitat_aber_falsche_seite_wird_verworfen(bestand, ohne_schwelle):
    """Eine falsche Seitenzahl macht die Fundstelle im Streitfall wertlos."""
    modell = Modell(
        MindAntwort(
            gefunden=True,
            antwort="Nebenleistung des Auftragnehmers.",
            fundstellen=[
                Fundstelle(datei="LV_Los4.pdf", seite=17, zitat=BELEG_ZITAT)
            ],
        )
    )

    ergebnis = beantworte_frage(FRAGE, dienste=_dienste(modell))

    assert ergebnis.gefunden is False
    assert ergebnis.hinweis == mind.GRUND_FALSCHE_QUELLE


def test_eine_falsche_von_zwei_fundstellen_kippt_die_ganze_antwort(bestand, ohne_schwelle):
    """Halb belegte Auskuenfte sind im Nachtragsstreit wertlos."""
    modell = Modell(
        MindAntwort(
            gefunden=True,
            antwort="Nebenleistung des Auftragnehmers; Behinderungen sind zu melden.",
            fundstellen=[
                Fundstelle(datei="LV_Los4.pdf", seite=4, zitat=BELEG_ZITAT),
                Fundstelle(
                    datei="LV_Los4.pdf",
                    seite=6,
                    zitat="Behinderungen sind binnen 24 Stunden per E-Mail zu melden.",
                ),
            ],
        )
    )

    ergebnis = beantworte_frage(FRAGE, dienste=_dienste(modell))

    assert ergebnis.gefunden is False
    assert ergebnis.fundstellen == []


def test_zu_kurzes_zitat_wird_verworfen(bestand, ohne_schwelle):
    """'Wasserhaltung' allein belegt nichts."""
    modell = Modell(
        MindAntwort(
            gefunden=True,
            antwort="Nebenleistung.",
            fundstellen=[
                Fundstelle(datei="LV_Los4.pdf", seite=4, zitat="Wasserhaltung")
            ],
        )
    )

    ergebnis = beantworte_frage(FRAGE, dienste=_dienste(modell))

    assert ergebnis.gefunden is False
    assert ergebnis.hinweis == mind.GRUND_ZITAT_ZU_KURZ


def test_leerer_antworttext_wird_verworfen(bestand, ohne_schwelle):
    modell = Modell(
        MindAntwort(
            gefunden=True,
            antwort="   ",
            fundstellen=[
                Fundstelle(datei="LV_Los4.pdf", seite=4, zitat=BELEG_ZITAT)
            ],
        )
    )

    ergebnis = beantworte_frage(FRAGE, dienste=_dienste(modell))

    assert ergebnis.gefunden is False
    assert ergebnis.hinweis == mind.GRUND_LEERE_ANTWORT


def test_modell_meldet_selbst_nichts_gefunden(bestand, ohne_schwelle):
    modell = Modell(MindAntwort(gefunden=False, antwort="", fundstellen=[]))

    ergebnis = beantworte_frage(FRAGE, dienste=_dienste(modell))

    assert ergebnis.gefunden is False
    assert ergebnis.antwort == ANTWORT_NICHTS_GEFUNDEN


def test_engine_ausfall_wird_nicht_zu_nichts_gefunden(bestand, ohne_schwelle):
    """Ein Ausfall ist keine Aussage ueber die Unterlagen - er geht nach oben."""
    modell = Modell(EngineFehler("kaputt"))

    with pytest.raises(EngineFehler):
        beantworte_frage(FRAGE, dienste=_dienste(modell))

    eintrag = _letzte_anfrage()
    assert eintrag.gefunden is False
    assert eintrag.verworfen_grund == mind.GRUND_ENGINE_FEHLER
    # Kein "dazu finde ich nichts" im Protokoll - es wurde nichts geprueft.
    assert eintrag.antwort == ""


# --- Vor dem Modellaufruf --------------------------------------------------


def test_ohne_belege_wird_das_modell_nicht_gefragt(datenbank):
    """Leere Wissensbank: gar nicht erst fragen."""
    modell = Modell(
        MindAntwort(gefunden=True, antwort="Irgendwas", fundstellen=[])
    )

    ergebnis = beantworte_frage("Gibt es Vorgaben zur Wasserhaltung?", dienste=_dienste(modell))

    assert modell.aufrufe == []
    assert ergebnis.gefunden is False
    assert ergebnis.hinweis == mind.GRUND_KEINE_BELEGE


def test_zu_ferne_treffer_fuehren_nicht_zum_modellaufruf(bestand, monkeypatch):
    monkeypatch.setattr(config, "MIND_MIN_AEHNLICHKEIT", 0.99)
    modell = Modell(MindAntwort(gefunden=True, antwort="Irgendwas", fundstellen=[]))

    ergebnis = beantworte_frage("Wie tief ist der Rohrgraben?", dienste=_dienste(modell))

    assert modell.aufrufe == []
    assert ergebnis.antwort == ANTWORT_NICHTS_GEFUNDEN


def test_leere_frage_wird_abgelehnt(datenbank):
    with pytest.raises(ValueError):
        beantworte_frage("   ")


# --- Prompt und Audit ------------------------------------------------------


def test_prompt_traegt_die_provenienz_jedes_ausschnitts(bestand, ohne_schwelle):
    modell = Modell(MindAntwort(gefunden=False, antwort="", fundstellen=[]))

    beantworte_frage(FRAGE, dienste=_dienste(modell))

    prompt = modell.aufrufe[0]
    assert "Datei: LV_Los4.pdf" in prompt
    assert "Seite: 4" in prompt
    assert "Abschnitt: 4 Wasserhaltung" in prompt
    assert FRAGE in prompt


def test_audit_haelt_frage_belege_und_verwerfungsgrund_fest(bestand, ohne_schwelle):
    modell = Modell(
        MindAntwort(gefunden=True, antwort="Ohne Beleg.", fundstellen=[])
    )

    beantworte_frage(FRAGE, baulos="SuedLink Baulos 4", dienste=_dienste(modell))

    eintrag = _letzte_anfrage()
    assert eintrag.frage == FRAGE
    assert eintrag.projekt == "SuedLink Baulos 4"
    assert eintrag.gefunden is False
    assert eintrag.verworfen_grund == mind.GRUND_OHNE_FUNDSTELLE
    # Die herangezogenen Chunks sind nachvollziehbar.
    assert eintrag.chunk_ids


def test_audit_haelt_die_belegte_antwort_fest(bestand, ohne_schwelle):
    modell = Modell(
        MindAntwort(
            gefunden=True,
            antwort="Nebenleistung des Auftragnehmers.",
            fundstellen=[Fundstelle(datei="LV_Los4.pdf", seite=4, zitat=BELEG_ZITAT)],
        )
    )

    beantworte_frage(FRAGE, dienste=_dienste(modell))

    eintrag = _letzte_anfrage()
    assert eintrag.gefunden is True
    assert eintrag.verworfen_grund is None
    assert eintrag.fundstellen_json[0]["seite"] == 4

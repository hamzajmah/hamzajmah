"""Mind - Wissensbank ueber alle Projektdokumente.

Zwei Ablaeufe: Dokumente indexieren und Fragen beantworten.

Die eiserne Regel aus CLAUDE.md steht ueber allem: **Mind zitiert immer die
Fundstelle. Keine Quelle - keine Antwort.** Durchgesetzt wird sie an drei
Stellen, absichtlich mehrfach:

1. Findet die Suche keine ausreichend relevanten Ausschnitte, wird das Modell
   gar nicht erst gefragt.
2. Das Ausgabeschema (``MindAntwort``) verlangt Fundstellen mit woertlichem
   Belegzitat.
3. **Nach** dem Modellaufruf prueft ``pruefe_fundstellen`` im Code nach: Jedes
   Zitat muss woertlich in einem der uebergebenen Ausschnitte stehen, und die
   ausgelieferte Fundstelle wird aus der Datenbank-Provenienz dieses
   Ausschnitts gebaut - nicht aus dem, was das Modell behauptet. Stimmt etwas
   nicht, faellt die gesamte Antwort und der Fragende bekommt
   "Dazu finde ich in den vorliegenden Unterlagen nichts."

Punkt 3 ist der eigentliche Schutz. Ein Prompt ist eine Bitte, kein Riegel.
"""

import datetime as dt
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

import httpx
from sqlalchemy import func, select

from . import chunking, config, db, dokumente, suche
from .dokumente import DokumentFehler
from .http_client import UpstreamFehler
from .openrouter import EngineFehler, erzeuge_vektoren, strukturierte_antwort
from .schemas import Fundstelle, MindAntwort

logger = logging.getLogger(__name__)

SCHEMA_NAME = "mind_antwort"

ANTWORT_NICHTS_GEFUNDEN = "Dazu finde ich in den vorliegenden Unterlagen nichts."

# Gruende, aus denen eine Modellantwort verworfen wurde. Sie stehen im
# Audit-Log, nicht in der Antwort an den Nutzer.
GRUND_KEINE_BELEGE = "keine ausreichend relevanten Ausschnitte gefunden"
GRUND_MODELL_OHNE_FUND = "Modell meldet selbst: nicht belegbar"
GRUND_OHNE_FUNDSTELLE = "Antwort ohne Fundstelle"
GRUND_LEERE_ANTWORT = "Antworttext leer"
GRUND_ZITAT_ZU_KURZ = "Belegzitat zu kurz"
GRUND_ZITAT_NICHT_GEFUNDEN = "Belegzitat steht in keinem uebergebenen Ausschnitt"
GRUND_FALSCHE_QUELLE = "Fundstelle nennt eine andere Quelle als das Belegzitat"
GRUND_ENGINE_FEHLER = "Engine nicht verfuegbar - Frage nicht beantwortet"


SYSTEM_PROMPT = """\
Du bist die Wissensbank eines deutschen Trassenbau-Unternehmens (Stromtrassen,
Pipelines, Netze, Schiene). Du beantwortest Fragen zu einem Bauprojekt
ausschliesslich aus den Textausschnitten, die dir mit der Frage uebergeben
werden. Dein Wissen aus dem Training ist hier ohne Bedeutung.

Eiserne Regeln:
1. Jede Aussage deiner Antwort muss durch mindestens eine Fundstelle belegt
   sein. Kein Beleg - keine Aussage.
2. Ein Belegzitat wird Zeichen fuer Zeichen aus dem Ausschnitt uebernommen.
   Nicht kuerzen, nicht glaetten, nicht zusammensetzen, nichts einfuegen.
   Waehle die Passage, die die Aussage wirklich traegt (ein bis drei Saetze).
3. "datei" und "seite" der Fundstelle uebernimmst du unveraendert aus dem
   Kopf des Ausschnitts, aus dem das Zitat stammt.
4. Steht die Antwort nicht in den Ausschnitten oder nur teilweise, setzt du
   gefunden=false, laesst antwort leer und fundstellen leer. Das ist eine
   richtige und erwuenschte Antwort - raten ist es nicht.
5. Rechne nicht, schaetze nicht, kombiniere keine Angaben zu neuen Zahlen und
   uebertrage nichts von einem Bauabschnitt auf einen anderen.
6. Widersprechen sich die Ausschnitte, benennst du den Widerspruch und belegst
   beide Seiten.
7. Antworte sachlich auf Deutsch, in der Fachsprache der Unterlagen, knapp und
   ohne Einleitungsfloskeln. Nenne dich selbst nicht und beschreibe nicht,
   wie du arbeitest.
"""

BENUTZER_PROMPT_VORLAGE = """\
Frage:
{frage}

Textausschnitte aus den Projektunterlagen:
{ausschnitte}

Beantworte die Frage ausschliesslich aus diesen Ausschnitten und belege jede
Aussage mit einer Fundstelle samt woertlichem Zitat. Ist die Frage aus den
Ausschnitten nicht zu beantworten: gefunden=false.
"""


# --- Aussenkontakte (in Tests ersetzbar) -----------------------------------


def _standard_vektoren(texte: list[str], client: httpx.Client | None = None) -> list[list[float]]:
    return erzeuge_vektoren(texte, modell=config.MODEL_EMBEDDING, client=client)


def _standard_antwort(
    system_prompt: str, benutzer_prompt: str, client: httpx.Client | None = None
) -> MindAntwort:
    return strukturierte_antwort(
        modell=config.MODEL_MIND,
        system_prompt=system_prompt,
        benutzer_prompt=benutzer_prompt,
        schema_name=SCHEMA_NAME,
        ausgabe_modell=MindAntwort,
        client=client,
    )


@dataclass
class MindDienste:
    """Austauschbare Aussenkontakte - in Tests durch Attrappen ersetzbar."""

    vektoren: Callable[[list[str]], list[list[float]]] = _standard_vektoren
    antworte: Callable[[str, str], MindAntwort] = _standard_antwort


# --- Indexierung -----------------------------------------------------------


@dataclass
class IndexBericht:
    """Was ein Indexlauf getan hat - ohne Dokumentinhalte."""

    geprueft: int = 0
    neu: int = 0
    aktualisiert: int = 0
    unveraendert: int = 0
    chunks: int = 0
    fehler: list[str] = field(default_factory=list)


def indexiere_wissensbank(
    verzeichnis: Optional[Path] = None,
    dienste: Optional[MindDienste] = None,
) -> IndexBericht:
    """Alle Dateien der Wissensbank einlesen, zerlegen und einbetten.

    Unveraenderte Dateien (gleicher Hash) werden uebersprungen; geaenderte
    Dateien verlieren zuerst ihre alten Chunks. Wiederholte Laeufe erzeugen
    damit keine Dubletten.
    """
    dienste = dienste or MindDienste()
    wurzel = verzeichnis or config.KNOWLEDGE_DIR
    bericht = IndexBericht()

    for pfad in dokumente.finde_dokumente(wurzel):
        bericht.geprueft += 1
        try:
            zustand, anzahl = indexiere_datei(pfad, wurzel, dienste)
        except (DokumentFehler, EngineFehler, chunking.ProvenienzFehler) as fehler:
            # Ein kaputtes Dokument darf den Lauf nicht abbrechen - es wird
            # vermerkt und bleibt sichtbar unindexiert.
            logger.warning("%s konnte nicht indexiert werden: %s", pfad.name, fehler)
            bericht.fehler.append(f"{pfad.name}: {fehler}")
            _vermerke_dokumentfehler(pfad, wurzel, str(fehler))
            continue
        if zustand == "neu":
            bericht.neu += 1
        elif zustand == "aktualisiert":
            bericht.aktualisiert += 1
        else:
            bericht.unveraendert += 1
        bericht.chunks += anzahl

    logger.info(
        "Indexlauf fertig: %s geprueft, %s neu, %s aktualisiert, %s unveraendert, "
        "%s Chunks, %s Fehler.",
        bericht.geprueft,
        bericht.neu,
        bericht.aktualisiert,
        bericht.unveraendert,
        bericht.chunks,
        len(bericht.fehler),
    )
    return bericht


def indexiere_datei(
    pfad: Path, wurzel: Path, dienste: MindDienste
) -> tuple[str, int]:
    """Eine Datei indexieren. Gibt Zustand ("neu"/"aktualisiert"/"unveraendert")
    und die Anzahl geschriebener Chunks zurueck."""
    relativer_pfad = _relativer_pfad(pfad, wurzel)
    datei_hash = dokumente.berechne_hash(pfad)

    with db.sitzung() as sitzung:
        vorhanden = db.finde_dokument_nach_pfad(sitzung, relativer_pfad)
        if (
            vorhanden is not None
            and vorhanden.datei_hash == datei_hash
            and vorhanden.status == db.DOKUMENT_INDEXIERT
            and vorhanden.chunk_anzahl > 0
        ):
            return "unveraendert", 0
        war_vorhanden = vorhanden is not None

    inhalt = dokumente.lese_dokument(pfad, wurzel)
    chunks = chunking.zerlege_dokument(inhalt)
    if not chunks:
        raise DokumentFehler(f"{pfad.name}: keine verwertbaren Textabschnitte.")

    vektoren = dienste.vektoren([chunk.text for chunk in chunks])
    if len(vektoren) != len(chunks):
        raise EngineFehler("Zu jedem Chunk muss genau ein Vektor gehoeren.")

    with db.sitzung() as sitzung:
        dokument = db.finde_dokument_nach_pfad(sitzung, relativer_pfad)
        if dokument is None:
            dokument = db.WissensDokument(pfad=relativer_pfad)
            sitzung.add(dokument)
        else:
            # Erst die alten Chunks weg - sonst steht die Datei doppelt im Index.
            entfernt = db.loesche_chunks(sitzung, dokument.id)
            logger.info("%s: %s alte Chunks entfernt.", inhalt.dateiname, entfernt)

        dokument.dateiname = inhalt.dateiname
        dokument.dateiformat = inhalt.dateiformat
        dokument.dokumenttyp = inhalt.dokumenttyp
        dokument.projekt = inhalt.projekt
        dokument.datei_hash = inhalt.datei_hash
        dokument.geaendert_am = inhalt.geaendert_am
        dokument.seiten_anzahl = len(inhalt.seiten)
        dokument.chunk_anzahl = len(chunks)
        dokument.status = db.DOKUMENT_INDEXIERT
        dokument.fehlermeldung = None
        sitzung.flush()

        for chunk, vektor in zip(chunks, vektoren):
            sitzung.add(
                db.WissensChunk(
                    dokument_id=dokument.id,
                    dateiname=chunk.dateiname,
                    projekt=inhalt.projekt,
                    dokumenttyp=inhalt.dokumenttyp,
                    seite=chunk.seite,
                    abschnitt=chunk.abschnitt,
                    position=chunk.position,
                    text=chunk.text,
                    text_klein=chunk.text.lower(),
                    geaendert_am=inhalt.geaendert_am,
                    embedding=vektor,
                )
            )

    return ("aktualisiert" if war_vorhanden else "neu"), len(chunks)


def _relativer_pfad(pfad: Path, wurzel: Path) -> str:
    try:
        return str(pfad.resolve().relative_to(wurzel.resolve()))
    except ValueError:
        return pfad.name


def _vermerke_dokumentfehler(pfad: Path, wurzel: Path, meldung: str) -> None:
    """Fehlgeschlagene Datei sichtbar halten - ohne Inhalte."""
    relativer_pfad = _relativer_pfad(pfad, wurzel)
    try:
        with db.sitzung() as sitzung:
            dokument = db.finde_dokument_nach_pfad(sitzung, relativer_pfad)
            if dokument is None:
                dokument = db.WissensDokument(
                    pfad=relativer_pfad,
                    dateiname=pfad.name,
                    dateiformat=dokumente.UNTERSTUETZTE_FORMATE.get(pfad.suffix.lower(), ""),
                    dokumenttyp=dokumente.dokumenttyp_aus_name(pfad.name),
                    projekt=dokumente.projekt_aus_pfad(pfad, wurzel),
                    datei_hash="",
                    geaendert_am=dt.datetime.now(dt.timezone.utc),
                )
                sitzung.add(dokument)
                sitzung.flush()
            else:
                db.loesche_chunks(sitzung, dokument.id)
            dokument.chunk_anzahl = 0
            dokument.status = db.DOKUMENT_FEHLER
            dokument.fehlermeldung = meldung[:1000]
    except Exception:  # noqa: BLE001 - der Fehlerpfad darf nicht selbst platzen
        logger.exception("Fehlerstatus fuer %s konnte nicht gespeichert werden.", pfad.name)


# --- Antwort mit Zitatpflicht ----------------------------------------------


@dataclass(frozen=True)
class MindErgebnis:
    """Was der Fragende bekommt - plus Innenansicht fuer das Audit."""

    gefunden: bool
    antwort: str
    fundstellen: list[Fundstelle]
    chunk_ids: list[int]
    # Nur intern: warum eine Modellantwort verworfen wurde.
    hinweis: Optional[str] = None


def beantworte_frage(
    frage: str,
    *,
    baulos: Optional[str] = None,
    dienste: Optional[MindDienste] = None,
) -> MindErgebnis:
    """Frage an die Wissensbank - mit Beleg oder gar nicht."""
    dienste = dienste or MindDienste()
    text = (frage or "").strip()
    if not text:
        raise ValueError("Leere Frage.")

    frage_vektor = _frage_vektor(text, dienste)

    with db.sitzung() as sitzung:
        treffer = suche.hybrid_suche(sitzung, text, frage_vektor, projekt=baulos)
        belege = suche.relevante_treffer(treffer)

    if not belege:
        logger.info("Keine belastbaren Treffer - Modell wird nicht befragt.")
        return _nichts_gefunden(text, baulos, [], GRUND_KEINE_BELEGE)

    chunk_ids = [beleg.chunk_id for beleg in belege]
    benutzer_prompt = BENUTZER_PROMPT_VORLAGE.format(
        frage=text, ausschnitte=baue_ausschnitte(belege)
    )
    try:
        roh = dienste.antworte(SYSTEM_PROMPT, benutzer_prompt)
    except (EngineFehler, UpstreamFehler):
        # Ein Ausfall der Engine ist ausdruecklich kein "dazu finde ich
        # nichts" - das waere eine Aussage ueber die Unterlagen, die wir nie
        # geprueft haben. Der Fehler geht sichtbar nach oben.
        logger.warning("Engine nicht verfuegbar - Frage bleibt unbeantwortet.")
        _schreibe_audit(
            text,
            baulos,
            MindErgebnis(
                gefunden=False,
                antwort="",
                fundstellen=[],
                chunk_ids=chunk_ids,
                hinweis=GRUND_ENGINE_FEHLER,
            ),
        )
        raise

    geprueft, grund = pruefe_fundstellen(roh, belege)
    if grund is not None:
        # Genau hier haelt die Zitatpflicht: Die Antwort mag plausibel klingen,
        # ohne pruefbaren Beleg wird sie nicht weitergegeben.
        logger.warning("Antwort verworfen (%s) - %s Belege lagen vor.", grund, len(belege))
        return _nichts_gefunden(text, baulos, chunk_ids, grund)

    logger.info("Frage beantwortet mit %s Fundstelle(n).", len(geprueft))
    ergebnis = MindErgebnis(
        gefunden=True,
        antwort=roh.antwort.strip(),
        fundstellen=geprueft,
        chunk_ids=chunk_ids,
    )
    _schreibe_audit(text, baulos, ergebnis)
    return ergebnis


def _frage_vektor(frage: str, dienste: MindDienste) -> Optional[list[float]]:
    """Vektor zur Frage - faellt der Dienst aus, bleibt die Volltextsuche.

    Bewusster Kompromiss: Ohne Embeddings sinkt die Trefferquote (nur noch
    woertliche Suche), die Belegpflicht bleibt unberuehrt. Eine Antwort wird
    dadurch nie falsch, hoechstens seltener gefunden - deshalb hier
    weiterarbeiten statt abbrechen.
    """
    try:
        vektoren = dienste.vektoren([frage])
    except (EngineFehler, UpstreamFehler):
        logger.warning("Vektor zur Frage nicht verfuegbar - nur Volltextsuche.")
        return None
    return vektoren[0] if vektoren else None


def baue_ausschnitte(belege: Sequence[suche.Treffer]) -> str:
    """Belegte Ausschnitte fuer den Prompt - jeder mit seiner Herkunft."""
    bloecke: list[str] = []
    for nummer, beleg in enumerate(belege, start=1):
        kopf = [f"[Ausschnitt {nummer}]", f"Datei: {beleg.dateiname}"]
        if beleg.seite is not None:
            kopf.append(f"Seite: {beleg.seite}")
        if beleg.abschnitt:
            kopf.append(f"Abschnitt: {beleg.abschnitt}")
        kopf.append(f"Dokumenttyp: {beleg.dokumenttyp}")
        bloecke.append("\n".join(kopf) + f"\nText:\n{beleg.text}")
    return "\n\n---\n\n".join(bloecke)


# --- Die Nachpruefung ------------------------------------------------------

_TYPOGRAFIE = {
    "„": '"', "“": '"', "”": '"', "»": '"', "«": '"',
    "‚": "'", "‘": "'", "’": "'", "›": "'", "‹": "'",
    "–": "-", "—": "-", "−": "-", " ": " ",
}
_LEERRAUM = re.compile(r"\s+")


def _normalisiert_mit_karte(text: str) -> tuple[str, list[int]]:
    """Vergleichsform plus Rueckverweis auf die Stelle im Originaltext.

    Die Karte ordnet jedem Zeichen der Vergleichsform seinen Ursprung zu.
    Damit laesst sich ein gefundenes Zitat anschliessend im Wortlaut des
    Dokuments ausschneiden - und nicht in der Wiedergabe des Modells.
    """
    zeichenfolge: list[str] = []
    karte: list[int] = []
    letztes_war_leerraum = True  # fuehrenden Leerraum ueberspringen
    for stelle, zeichen in enumerate(text):
        ersetzt = _TYPOGRAFIE.get(zeichen, zeichen)
        if ersetzt.isspace():
            if not letztes_war_leerraum:
                zeichenfolge.append(" ")
                karte.append(stelle)
                letztes_war_leerraum = True
            continue
        letztes_war_leerraum = False
        for teilzeichen in ersetzt.casefold():
            zeichenfolge.append(teilzeichen)
            karte.append(stelle)
    while zeichenfolge and zeichenfolge[-1] == " ":
        zeichenfolge.pop()
        karte.pop()
    return "".join(zeichenfolge), karte


def vergleichbar(text: str) -> str:
    """Text auf eine Form bringen, in der sich Zitate vergleichen lassen.

    Zeilenumbrueche aus dem PDF, Anfuehrungszeichen und Gross-/Kleinschreibung
    duerfen einen echten Beleg nicht scheitern lassen. Alles darueber hinaus
    wird nicht geglaettet - sonst waere die Pruefung nichts mehr wert.
    """
    return _normalisiert_mit_karte(unicodedata.normalize("NFKC", text or ""))[0]


def originalzitat(chunktext: str, zitat: str) -> Optional[str]:
    """Das Zitat im Wortlaut des Dokuments, oder None wenn es nicht dort steht.

    Ausgeliefert wird der Ausschnitt aus dem Dokument, nicht die Abschrift des
    Modells: Im Nachtragsstreit zaehlt, was in der Unterlage steht.
    """
    # Chunktext ist bereits normalisiert (siehe dokumente.saeubere_text),
    # deshalb ohne erneutes NFKC - sonst passt die Karte nicht mehr.
    vergleichstext, karte = _normalisiert_mit_karte(chunktext)
    gesucht = vergleichbar(zitat)
    if not gesucht:
        return None
    stelle = vergleichstext.find(gesucht)
    if stelle < 0:
        return None
    return chunktext[karte[stelle] : karte[stelle + len(gesucht) - 1] + 1].strip()


def _dateiname_gleich(links: str, rechts: str) -> bool:
    return links.strip().casefold() == rechts.strip().casefold()


def pruefe_fundstellen(
    antwort: MindAntwort,
    belege: Sequence[suche.Treffer],
) -> tuple[list[Fundstelle], Optional[str]]:
    """Nachpruefung im Code - der eigentliche Riegel vor der Zitatpflicht.

    Fuer jede Fundstelle gilt: Das Belegzitat muss woertlich (bis auf
    Leerraum, Anfuehrungszeichen und Gross-/Kleinschreibung) in einem der
    uebergebenen Ausschnitte stehen. Die ausgelieferte Fundstelle wird
    anschliessend aus der Provenienz genau dieses Ausschnitts gebaut - das
    Modell kann Datei, Seite oder Abschnitt also nicht erfinden.

    Stimmt eine einzige Fundstelle nicht, faellt die ganze Antwort. Halb
    belegte Auskuenfte sind im Nachtragsstreit wertlos.

    Rueckgabe: gepruefte Fundstellen und - falls verworfen - der Grund.
    """
    if not antwort.gefunden:
        return [], GRUND_MODELL_OHNE_FUND
    if not antwort.fundstellen:
        return [], GRUND_OHNE_FUNDSTELLE
    if not antwort.antwort.strip():
        return [], GRUND_LEERE_ANTWORT

    vorbereitet = [(beleg, vergleichbar(beleg.text)) for beleg in belege]
    geprueft: list[Fundstelle] = []
    gesehen: set[tuple[int, str]] = set()

    for fundstelle in antwort.fundstellen:
        zitat = vergleichbar(fundstelle.zitat)
        if len(zitat) < config.MIND_MIN_ZITAT_ZEICHEN:
            return [], GRUND_ZITAT_ZU_KURZ

        quellen = [beleg for beleg, text in vorbereitet if zitat in text]
        if not quellen:
            return [], GRUND_ZITAT_NICHT_GEFUNDEN

        # Das Modell darf das Zitat nicht der falschen Datei/Seite zuordnen.
        passend = [
            beleg
            for beleg in quellen
            if _dateiname_gleich(beleg.dateiname, fundstelle.datei)
            and (fundstelle.seite is None or beleg.seite == fundstelle.seite)
        ]
        if not passend:
            return [], GRUND_FALSCHE_QUELLE

        quelle = passend[0]
        schluessel = (quelle.chunk_id, zitat)
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        geprueft.append(
            Fundstelle(
                datei=quelle.dateiname,
                seite=quelle.seite,
                abschnitt=quelle.abschnitt,
                # Wortlaut aus der Unterlage, nicht aus der Antwort.
                zitat=originalzitat(quelle.text, fundstelle.zitat) or fundstelle.zitat.strip(),
            )
        )

    if not geprueft:
        return [], GRUND_OHNE_FUNDSTELLE
    return geprueft, None


def _nichts_gefunden(
    frage: str, baulos: Optional[str], chunk_ids: list[int], grund: str
) -> MindErgebnis:
    ergebnis = MindErgebnis(
        gefunden=False,
        antwort=ANTWORT_NICHTS_GEFUNDEN,
        fundstellen=[],
        chunk_ids=chunk_ids,
        hinweis=grund,
    )
    _schreibe_audit(frage, baulos, ergebnis)
    return ergebnis


def _schreibe_audit(frage: str, baulos: Optional[str], ergebnis: MindErgebnis) -> None:
    """Audit-Log in die Datenbank: Frage, Belege, Antwort, Verwerfungsgrund.

    Absichtlich nicht ins Anwendungslog - Frage und Antwort sind Kundendaten.
    Ein Fehler beim Protokollieren darf die Antwort nicht verhindern.
    """
    try:
        with db.sitzung() as sitzung:
            sitzung.add(
                db.MindAnfrage(
                    projekt=baulos,
                    frage=frage,
                    chunk_ids=ergebnis.chunk_ids,
                    gefunden=ergebnis.gefunden,
                    antwort=ergebnis.antwort,
                    fundstellen_json=[
                        fundstelle.model_dump(mode="json")
                        for fundstelle in ergebnis.fundstellen
                    ],
                    verworfen_grund=ergebnis.hinweis,
                )
            )
    except Exception:  # noqa: BLE001
        logger.exception("Audit-Eintrag konnte nicht geschrieben werden.")


def wissensbank_status() -> dict[str, object]:
    """Kennzahlen des Index - fuer die Statusroute, ohne Dokumentinhalte."""
    with db.sitzung() as sitzung:
        dokumente_gesamt = sitzung.scalar(select(func.count(db.WissensDokument.id))) or 0
        fehlerhaft = (
            sitzung.scalar(
                select(func.count(db.WissensDokument.id)).where(
                    db.WissensDokument.status == db.DOKUMENT_FEHLER
                )
            )
            or 0
        )
        chunks = sitzung.scalar(select(func.count(db.WissensChunk.id))) or 0
        zuletzt = sitzung.scalar(select(func.max(db.WissensDokument.indexiert_am)))
        projekte = list(
            sitzung.scalars(
                select(db.WissensDokument.projekt).distinct().order_by(db.WissensDokument.projekt)
            ).all()
        )

    return {
        "dokumente": dokumente_gesamt,
        "dokumente_mit_fehler": fehlerhaft,
        "chunks": chunks,
        "projekte": projekte,
        "zuletzt_indexiert": zuletzt.isoformat() if zuletzt else None,
    }

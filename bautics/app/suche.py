"""Mind, Schritt 3: Hybrid-Suche ueber die Wissensbank.

Warum zwei Verfahren statt nur Vektoren?

Bedeutungssuche findet die richtige Stelle, wenn der Fragende andere Worte
benutzt als das Dokument ("Wer haftet fuer die Wasserhaltung?" -> Abschnitt
"Grundwasserabsenkung"). Sie ist aber genau dort schwach, wo es im Bauwesen
haeufig darauf ankommt: bei Zeichenketten ohne Bedeutung. Eine Positionsnummer
wie ``03.02.040`` oder eine Stationierung wie ``12+400`` liegt im Vektorraum
neben ``03.02.050`` und ``12+700`` - die exakte Textsuche trifft sie dagegen
zuverlaessig. Deshalb laufen beide Verfahren und ihre Ergebnislisten werden per
Reciprocal Rank Fusion zusammengefuehrt.

Zielaufbau ist Postgres mit pgvector; ist der nicht verfuegbar (SQLite in
Entwicklung und Demo), rechnet ein Fallback die Kosinus-Aehnlichkeit in Python.
"""

import logging
import math
import re
from dataclasses import dataclass, replace
from typing import Optional, Sequence

from sqlalchemy import Select, or_, select
from sqlalchemy import text as sql_text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import config
from .db import WissensChunk

logger = logging.getLogger(__name__)

# Zeichenketten, bei denen die exakte Textsuche der Bedeutungssuche ueberlegen
# ist: Positionsnummern (03.02.040), Stationierungen (12+400), Paragraphen
# (§ 6), Normen (DIN 18300), Nachtragsnummern (N-012).
_HARTE_MUSTER = (
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){1,4}\b"),
    re.compile(r"\b\d{1,4}\+\d{3}\b"),
    re.compile(r"§\s*\d+[a-z]?"),
    re.compile(r"\b(?:DIN|EN|ISO|ZTV|VOB)[ /-]?[A-Z]?\s?\d{2,5}\b", re.IGNORECASE),
    re.compile(r"\b[A-Z]{1,3}-\d{2,5}\b"),
)

# Fuellwoerter, die als Suchbegriff nichts beitragen.
_STOPPWOERTER = frozenset(
    {
        "aber", "auch", "auf", "aus", "bei", "beim", "bis", "das", "dass", "dem",
        "den", "der", "des", "die", "durch", "ein", "eine", "einem", "einen",
        "einer", "eines", "fuer", "für", "gibt", "hat", "ich", "ist", "kann",
        "mit", "nach", "nicht", "noch", "oder", "sich", "sind", "und", "vom",
        "von", "vor", "was", "wann", "welche", "welchem", "welchen", "welcher",
        "wer", "wie", "wieviel", "wird", "wo", "zum", "zur", "über", "ueber",
    }
)

_WORT = re.compile(r"[\wÄÖÜäöüß+.\-/§]{2,}", re.UNICODE)


@dataclass(frozen=True)
class Treffer:
    """Ein Suchtreffer mit vollstaendiger Provenienz."""

    chunk_id: int
    dateiname: str
    seite: Optional[int]
    abschnitt: Optional[str]
    projekt: str
    dokumenttyp: str
    text: str
    # Kosinus-Aehnlichkeit (-1..1); None, wenn nur die Volltextsuche traf.
    aehnlichkeit: Optional[float] = None
    # True, wenn eine Positionsnummer/Stationierung woertlich im Text steht.
    exakter_fund: bool = False
    score: float = 0.0


# --- Begriffe aus der Frage ------------------------------------------------


def harte_begriffe(frage: str) -> list[str]:
    """Positionsnummern, Stationierungen, Paragraphen und Normen aus der Frage."""
    gefunden: list[str] = []
    for muster in _HARTE_MUSTER:
        for treffer in muster.findall(frage):
            begriff = re.sub(r"\s+", " ", treffer).strip()
            if begriff and begriff.lower() not in {g.lower() for g in gefunden}:
                gefunden.append(begriff)
    return gefunden


def suchbegriffe(frage: str) -> list[str]:
    """Alle Suchbegriffe der Volltextsuche - harte Begriffe zuerst."""
    begriffe = harte_begriffe(frage)
    bekannt = {begriff.lower() for begriff in begriffe}
    for wort in _WORT.findall(frage):
        klein = wort.lower().strip(".,;:!?")
        if len(klein) < 4 or klein in _STOPPWOERTER or klein in bekannt:
            continue
        bekannt.add(klein)
        begriffe.append(klein)
    return begriffe


def _like_muster(begriff: str) -> str:
    """LIKE-Sonderzeichen entschaerfen - Positionsnummern enthalten Punkte."""
    entschaerft = begriff.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{entschaerft.lower()}%"


# --- Volltextsuche ---------------------------------------------------------


def _grundabfrage(projekt: Optional[str]) -> Select:
    abfrage = select(WissensChunk)
    if projekt:
        abfrage = abfrage.where(WissensChunk.projekt == projekt)
    return abfrage


def volltext_treffer(
    sitzung: Session,
    frage: str,
    *,
    projekt: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[Treffer]:
    """Exakte Textsuche ueber die Chunks.

    Bewusst als LIKE ueber alle Suchbegriffe und nicht als Postgres-Volltext:
    Die deutsche Textsuche zerlegt ``03.02.040`` in Bestandteile und findet die
    Position dann gerade nicht mehr zuverlaessig.

    TODO Produktion: Bei grossen Bestaenden braucht das einen Index
    (pg_trgm/GIN); ein sequenzieller Scan ueber alle Chunks skaliert nicht.
    """
    limit = limit or config.MIND_KANDIDATEN
    begriffe = suchbegriffe(frage)
    if not begriffe:
        return []

    harte = {begriff.lower() for begriff in harte_begriffe(frage)}
    bedingungen = [
        WissensChunk.text_klein.like(_like_muster(begriff), escape="\\")
        for begriff in begriffe
    ]
    # Mehr Kandidaten holen als am Ende gebraucht werden, damit die Bewertung
    # in Python noch etwas zu sortieren hat.
    kandidaten = (
        sitzung.scalars(_grundabfrage(projekt).where(or_(*bedingungen)).limit(limit * 5))
        .unique()
        .all()
    )

    bewertet: list[tuple[float, bool, WissensChunk]] = []
    for chunk in kandidaten:
        kleintext = chunk.text_klein
        punkte = 0.0
        exakt = False
        for begriff in begriffe:
            if begriff.lower() in kleintext:
                # Harte Begriffe wiegen schwerer - sie sind der Grund fuer
                # dieses zweite Suchverfahren.
                if begriff.lower() in harte:
                    punkte += 3.0
                    exakt = True
                else:
                    punkte += 1.0
        if punkte:
            bewertet.append((punkte, exakt, chunk))

    bewertet.sort(key=lambda eintrag: (-eintrag[0], len(eintrag[2].text)))
    return [
        _zu_treffer(chunk, exakter_fund=exakt) for _, exakt, chunk in bewertet[:limit]
    ]


# --- Vektorsuche -----------------------------------------------------------


def kosinus(links: Sequence[float], rechts: Sequence[float]) -> float:
    """Kosinus-Aehnlichkeit zweier Vektoren; 0.0 bei Laengenunterschied."""
    if not links or not rechts or len(links) != len(rechts):
        return 0.0
    produkt = sum(a * b for a, b in zip(links, rechts))
    betrag_links = math.sqrt(sum(a * a for a in links))
    betrag_rechts = math.sqrt(sum(b * b for b in rechts))
    if betrag_links == 0.0 or betrag_rechts == 0.0:
        return 0.0
    return produkt / (betrag_links * betrag_rechts)


def pgvector_verfuegbar(sitzung: Session) -> bool:
    """Laeuft die Suche auf Postgres mit installierter pgvector-Erweiterung?"""
    bindung = sitzung.get_bind()
    if bindung is None or bindung.dialect.name != "postgresql":
        return False
    try:
        return bool(
            sitzung.scalar(sql_text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
        )
    except SQLAlchemyError:  # pragma: no cover - nur auf echtem Postgres
        logger.warning("pgvector-Pruefung fehlgeschlagen, nutze Python-Fallback.")
        return False


def vektor_treffer(
    sitzung: Session,
    frage_vektor: Sequence[float],
    *,
    projekt: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[Treffer]:
    """Bedeutungssuche - mit pgvector, sonst im Python-Fallback."""
    limit = limit or config.MIND_KANDIDATEN
    if not frage_vektor:
        return []
    if pgvector_verfuegbar(sitzung):
        return _vektor_treffer_pgvector(sitzung, frage_vektor, projekt=projekt, limit=limit)
    return _vektor_treffer_python(sitzung, frage_vektor, projekt=projekt, limit=limit)


def _vektor_treffer_pgvector(
    sitzung: Session,
    frage_vektor: Sequence[float],
    *,
    projekt: Optional[str],
    limit: int,
) -> list[Treffer]:  # pragma: no cover - braucht echtes Postgres mit pgvector
    """Aehnlichkeitssuche in der Datenbank (Kosinus-Distanz ``<=>``)."""
    vektor_literal = "[" + ",".join(repr(float(zahl)) for zahl in frage_vektor) + "]"
    projektfilter = "AND projekt = :projekt" if projekt else ""
    anweisung = sql_text(
        f"""
        SELECT id, 1 - (embedding <=> CAST(:vektor AS vector)) AS aehnlichkeit
        FROM wissens_chunks
        WHERE embedding IS NOT NULL {projektfilter}
        ORDER BY embedding <=> CAST(:vektor AS vector)
        LIMIT :limit
        """
    )
    parameter: dict[str, object] = {"vektor": vektor_literal, "limit": limit}
    if projekt:
        parameter["projekt"] = projekt

    zeilen = sitzung.execute(anweisung, parameter).all()
    if not zeilen:
        return []
    aehnlichkeiten = {zeile.id: float(zeile.aehnlichkeit) for zeile in zeilen}
    chunks = sitzung.scalars(
        select(WissensChunk).where(WissensChunk.id.in_(list(aehnlichkeiten)))
    ).all()
    treffer = [_zu_treffer(chunk, aehnlichkeit=aehnlichkeiten[chunk.id]) for chunk in chunks]
    treffer.sort(key=lambda eintrag: -(eintrag.aehnlichkeit or 0.0))
    return treffer


def _vektor_treffer_python(
    sitzung: Session,
    frage_vektor: Sequence[float],
    *,
    projekt: Optional[str],
    limit: int,
) -> list[Treffer]:
    """Fallback: alle Vektoren laden und in Python vergleichen.

    Ausdruecklich nur fuer Entwicklung und Demo (SQLite). Der Fallback liest
    saemtliche Embeddings der Wissensbank in den Speicher und rechnet linear -
    das ist bei einigen hundert Chunks in Ordnung und bei einem echten
    Projektbestand nicht mehr vertretbar. Produktion laeuft ueber pgvector.
    """
    chunks = sitzung.scalars(
        _grundabfrage(projekt).where(WissensChunk.embedding.is_not(None))
    ).all()
    if len(chunks) > 5000:
        logger.warning(
            "Vektor-Fallback ueber %s Chunks - fuer Produktion pgvector einrichten.",
            len(chunks),
        )
    bewertet = [
        (kosinus(frage_vektor, chunk.embedding or []), chunk) for chunk in chunks
    ]
    bewertet.sort(key=lambda eintrag: -eintrag[0])
    return [
        _zu_treffer(chunk, aehnlichkeit=wert) for wert, chunk in bewertet[:limit]
    ]


def _zu_treffer(
    chunk: WissensChunk,
    *,
    aehnlichkeit: Optional[float] = None,
    exakter_fund: bool = False,
) -> Treffer:
    return Treffer(
        chunk_id=chunk.id,
        dateiname=chunk.dateiname,
        seite=chunk.seite,
        abschnitt=chunk.abschnitt,
        projekt=chunk.projekt,
        dokumenttyp=chunk.dokumenttyp,
        text=chunk.text,
        aehnlichkeit=aehnlichkeit,
        exakter_fund=exakter_fund,
    )


# --- Zusammenfuehrung ------------------------------------------------------


def fusioniere(
    listen: Sequence[Sequence[Treffer]], *, k: Optional[int] = None
) -> list[Treffer]:
    """Reciprocal Rank Fusion: ``score = summe(1 / (k + rang))``.

    RRF vergleicht nur Raenge, keine Punktzahlen - genau richtig hier, weil
    Kosinus-Aehnlichkeit und Trefferzahl der Textsuche keine gemeinsame Skala
    haben. Ein Chunk, den beide Verfahren mittelmaessig bewerten, steigt damit
    ueber einen, den nur eines vorne sieht.
    """
    k = k if k is not None else config.MIND_RRF_K
    punkte: dict[int, float] = {}
    beste: dict[int, Treffer] = {}

    for liste in listen:
        for rang, treffer in enumerate(liste, start=1):
            punkte[treffer.chunk_id] = punkte.get(treffer.chunk_id, 0.0) + 1.0 / (k + rang)
            vorhanden = beste.get(treffer.chunk_id)
            if vorhanden is None:
                beste[treffer.chunk_id] = treffer
                continue
            # Derselbe Chunk aus beiden Verfahren: jede Liste steuert bei, was
            # nur sie weiss (Aehnlichkeit bzw. exakter Fund).
            beste[treffer.chunk_id] = replace(
                vorhanden,
                aehnlichkeit=(
                    vorhanden.aehnlichkeit
                    if vorhanden.aehnlichkeit is not None
                    else treffer.aehnlichkeit
                ),
                exakter_fund=vorhanden.exakter_fund or treffer.exakter_fund,
            )

    zusammengefuehrt = [
        replace(treffer, score=punkte[chunk_id]) for chunk_id, treffer in beste.items()
    ]
    zusammengefuehrt.sort(key=lambda treffer: (-treffer.score, treffer.chunk_id))
    return zusammengefuehrt


def hybrid_suche(
    sitzung: Session,
    frage: str,
    frage_vektor: Optional[Sequence[float]],
    *,
    projekt: Optional[str] = None,
    kandidaten: Optional[int] = None,
) -> list[Treffer]:
    """Bedeutungssuche und Volltextsuche, per RRF zusammengefuehrt."""
    kandidaten = kandidaten or config.MIND_KANDIDATEN
    aus_vektoren = (
        vektor_treffer(sitzung, frage_vektor, projekt=projekt, limit=kandidaten)
        if frage_vektor
        else []
    )
    aus_volltext = volltext_treffer(sitzung, frage, projekt=projekt, limit=kandidaten)
    ergebnis = fusioniere([aus_vektoren, aus_volltext])
    logger.info(
        "Suche: %s Vektor-, %s Volltext-, %s zusammengefuehrte Treffer.",
        len(aus_vektoren),
        len(aus_volltext),
        len(ergebnis),
    )
    return ergebnis


def relevante_treffer(
    treffer: Sequence[Treffer],
    *,
    min_aehnlichkeit: Optional[float] = None,
    hoechstens: Optional[int] = None,
) -> list[Treffer]:
    """Aussortieren, was zu weit weg ist.

    Ein Treffer zaehlt, wenn er entweder inhaltlich nah genug ist oder einen
    harten Begriff (Positionsnummer, Stationierung) woertlich enthaelt. Bleibt
    nichts uebrig, wird das Modell gar nicht erst gefragt - eine Antwort ohne
    Grundlage waere die eine Sache, die Mind nicht tun darf.
    """
    schwelle = (
        min_aehnlichkeit if min_aehnlichkeit is not None else config.MIND_MIN_AEHNLICHKEIT
    )
    hoechstens = hoechstens or config.MIND_BELEGE
    gefiltert = [
        eintrag
        for eintrag in treffer
        if eintrag.exakter_fund
        or (eintrag.aehnlichkeit is not None and eintrag.aehnlichkeit >= schwelle)
    ]
    return gefiltert[:hoechstens]

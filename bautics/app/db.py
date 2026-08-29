"""Persistenz: Tagesberichte (Echo) und Wissensbank (Mind), SQLAlchemy.

Fuer den Durchstich reicht SQLite; ueber ``BAUTICS_DATABASE_URL`` laesst sich
ohne Codeaenderung auf Postgres umstellen. Ziel-Datenbank ist Postgres mit
pgvector - die Embedding-Spalte ist deshalb dialektabhaengig (siehe ``Vektor``).
"""

import datetime as dt
import json
import logging
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    func,
    select,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import UserDefinedType

from . import config

logger = logging.getLogger(__name__)

# Lebenszyklus eines Berichts
STATUS_EMPFANGEN = "empfangen"
STATUS_FERTIG = "fertig"
STATUS_FEHLER = "fehler"


class Base(DeclarativeBase):
    pass


def _jetzt() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Tagesbericht(Base):
    """Ein Tagesbericht von der Sprachnachricht bis zum fertigen Text."""

    __tablename__ = "tagesberichte"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Twilio-MessageSid: Schluessel fuer Idempotenz bei Webhook-Wiederholungen
    nachricht_sid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    absender: Mapped[str] = mapped_column(String(64))
    projekt: Mapped[str] = mapped_column(String(200), index=True)
    datum: Mapped[dt.date] = mapped_column(Date, index=True)
    berichtsnummer: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    status: Mapped[str] = mapped_column(String(20), default=STATUS_EMPFANGEN)
    rohtranskript: Mapped[Optional[str]] = mapped_column(Text, default=None)
    daten_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=None)
    berichtstext: Mapped[Optional[str]] = mapped_column(Text, default=None)
    fehlermeldung: Mapped[Optional[str]] = mapped_column(Text, default=None)
    erstellt_am: Mapped[dt.datetime] = mapped_column(DateTime, default=_jetzt)
    aktualisiert_am: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_jetzt, onupdate=_jetzt
    )

    def __repr__(self) -> str:  # pragma: no cover - nur Diagnose
        # Bewusst ohne Inhalte: Repr landet schnell in Logs.
        return f"<Tagesbericht id={self.id} status={self.status}>"


def erzeuge_engine(url: str | None = None) -> Engine:
    datenbank_url = url or config.DATABASE_URL
    verbindungsargumente: dict[str, Any] = {}
    if datenbank_url.startswith("sqlite"):
        # Hintergrundaufgaben laufen in einem anderen Thread als der Request.
        verbindungsargumente["check_same_thread"] = False
    return create_engine(datenbank_url, connect_args=verbindungsargumente, future=True)


_engine: Engine | None = None
_sitzungsfabrik: sessionmaker[Session] | None = None


def setze_engine(engine: Engine) -> None:
    """Engine austauschen - fuer Tests und alternative Einstiegspunkte."""
    global _engine, _sitzungsfabrik
    _engine = engine
    _sitzungsfabrik = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def hole_engine() -> Engine:
    if _engine is None:
        setze_engine(erzeuge_engine())
    assert _engine is not None
    return _engine


def hole_sitzungsfabrik() -> "sessionmaker[Session]":
    hole_engine()
    assert _sitzungsfabrik is not None
    return _sitzungsfabrik


def init_db() -> None:
    """Tabellen anlegen, falls sie fehlen (Migrationen kommen spaeter)."""
    Base.metadata.create_all(hole_engine())


@contextmanager
def sitzung() -> Iterator[Session]:
    """Sitzung mit Commit/Rollback-Klammer."""
    with hole_sitzungsfabrik()() as offene_sitzung:
        try:
            yield offene_sitzung
            offene_sitzung.commit()
        except Exception:
            offene_sitzung.rollback()
            raise


def finde_nach_sid(sitzung_: Session, nachricht_sid: str) -> Tagesbericht | None:
    """Idempotenz-Check: gab es diese WhatsApp-Nachricht schon einmal?"""
    return sitzung_.scalar(
        select(Tagesbericht).where(Tagesbericht.nachricht_sid == nachricht_sid)
    )


def naechste_berichtsnummer(sitzung_: Session, projekt: str, datum: dt.date) -> int:
    """Fortlaufende Nummer je Baulos und Kalenderjahr, beginnend bei 1.

    TODO: Bei parallelen Arbeitern und Postgres reicht das MAX+1 nicht - dann
    eine Sequenz je Baulos oder eine Unique-Bedingung (projekt, jahr, nummer)
    mit Wiederholung ergaenzen.
    """
    jahresbeginn = dt.date(datum.year, 1, 1)
    jahresende = dt.date(datum.year, 12, 31)
    hoechste = sitzung_.scalar(
        select(func.max(Tagesbericht.berichtsnummer)).where(
            Tagesbericht.projekt == projekt,
            Tagesbericht.datum >= jahresbeginn,
            Tagesbericht.datum <= jahresende,
        )
    )
    return (hoechste or 0) + 1


# --- Mind: Wissensbank -----------------------------------------------------

# Zustand eines Dokuments im Index
DOKUMENT_INDEXIERT = "indexiert"
DOKUMENT_FEHLER = "fehler"


class Vektor(UserDefinedType):
    """Embedding-Spalte, die auf Postgres pgvector nutzt und sonst Text.

    Auf Postgres entsteht eine echte ``vector(n)``-Spalte, auf der die
    Aehnlichkeitssuche mit ``<=>`` (und spaeter ein HNSW-Index) laeuft. Auf
    SQLite - unserem Entwicklungs- und Demo-Aufbau - wird derselbe Wert als
    Text gespeichert. Das Serialisierungsformat ``[0.1,0.2]`` ist bewusst fuer
    beide gleich: pgvector versteht es, und es ist gueltiges JSON.

    Voraussetzung auf Postgres: ``CREATE EXTENSION IF NOT EXISTS vector;``
    TODO Produktion: Index anlegen, z.B.
    ``CREATE INDEX ON wissens_chunks USING hnsw (embedding vector_cosine_ops);``
    """

    cache_ok = True

    def __init__(self, dimensionen: int) -> None:
        self.dimensionen = dimensionen

    # Signaturen ohne Typannotationen: so verlangt es SQLAlchemy.
    def bind_processor(self, dialect):
        def verarbeite(wert):
            if wert is None:
                return None
            return "[" + ",".join(repr(float(zahl)) for zahl in wert) + "]"

        return verarbeite

    def result_processor(self, dialect, coltype):
        def verarbeite(wert):
            if wert is None:
                return None
            if isinstance(wert, (list, tuple)):
                return [float(zahl) for zahl in wert]
            return [float(zahl) for zahl in json.loads(wert)]

        return verarbeite


@compiles(Vektor)
def _vektor_ddl(typ: Vektor, compiler: Any, **kw: Any) -> str:  # pragma: no cover - DDL
    return "TEXT"


@compiles(Vektor, "postgresql")
def _vektor_ddl_postgres(typ: Vektor, compiler: Any, **kw: Any) -> str:  # pragma: no cover
    return f"vector({typ.dimensionen})"


class WissensDokument(Base):
    """Eine Datei der Wissensbank - Zustand ihrer Indexierung.

    Der Datei-Hash entscheidet, ob neu indexiert werden muss. Identitaet ist
    der Pfad relativ zur Wissensbank-Wurzel, nicht der blosse Dateiname:
    "Bauvertrag.pdf" darf es in jedem Baulos einmal geben.
    """

    __tablename__ = "wissens_dokumente"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pfad: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    dateiname: Mapped[str] = mapped_column(String(300), index=True)
    dateiformat: Mapped[str] = mapped_column(String(10))
    dokumenttyp: Mapped[str] = mapped_column(String(60))
    projekt: Mapped[str] = mapped_column(String(200), index=True)
    datei_hash: Mapped[str] = mapped_column(String(64), index=True)
    geaendert_am: Mapped[dt.datetime] = mapped_column(DateTime)
    seiten_anzahl: Mapped[int] = mapped_column(Integer, default=0)
    chunk_anzahl: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=DOKUMENT_INDEXIERT)
    fehlermeldung: Mapped[Optional[str]] = mapped_column(Text, default=None)
    indexiert_am: Mapped[dt.datetime] = mapped_column(DateTime, default=_jetzt, onupdate=_jetzt)

    def __repr__(self) -> str:  # pragma: no cover - nur Diagnose
        return f"<WissensDokument id={self.id} status={self.status}>"


class WissensChunk(Base):
    """Ein zitierfaehiger Textausschnitt.

    Die Provenienz (Datei, Seite, Abschnitt, Projekt, Dokumenttyp) steht
    absichtlich am Chunk selbst und nicht nur am Dokument: Die Fundstelle wird
    beim Antworten aus genau dieser Zeile gebaut und gegengeprueft.
    """

    __tablename__ = "wissens_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dokument_id: Mapped[int] = mapped_column(
        ForeignKey("wissens_dokumente.id", ondelete="CASCADE"), index=True
    )
    dateiname: Mapped[str] = mapped_column(String(300), index=True)
    projekt: Mapped[str] = mapped_column(String(200), index=True)
    dokumenttyp: Mapped[str] = mapped_column(String(60))
    seite: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    abschnitt: Mapped[Optional[str]] = mapped_column(String(300), default=None)
    position: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    # Kleingeschriebene Kopie fuer die Volltextsuche. Bewusst als Spalte und
    # nicht als LOWER() in der Abfrage: SQLite kennt nur ASCII-Kleinschreibung
    # ("Uebergabe" mit Umlaut wuerde nicht treffen), Python kann Unicode. So
    # verhaelt sich die Suche auf SQLite und Postgres gleich und laesst sich
    # spaeter indexieren (pg_trgm/GIN).
    text_klein: Mapped[str] = mapped_column(Text)
    geaendert_am: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, default=None)
    embedding: Mapped[Optional[list[float]]] = mapped_column(
        Vektor(config.EMBEDDING_DIMENSIONEN), default=None
    )

    def __repr__(self) -> str:  # pragma: no cover - nur Diagnose
        # Ohne Text: Repr landet schnell in Logs, der Inhalt ist Kundendatum.
        return f"<WissensChunk id={self.id} datei={self.dateiname} seite={self.seite}>"


class MindAnfrage(Base):
    """Audit-Log: Wer hat was gefragt, worauf stuetzte sich die Antwort?

    Bewusst in der Datenbank und nicht im Anwendungslog - Frage und Antwort
    sind Kundendaten. In die normalen Logs gehen nur IDs und Zaehler.
    """

    __tablename__ = "mind_anfragen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gestellt_am: Mapped[dt.datetime] = mapped_column(DateTime, default=_jetzt, index=True)
    projekt: Mapped[Optional[str]] = mapped_column(String(200), default=None, index=True)
    frage: Mapped[str] = mapped_column(Text)
    chunk_ids: Mapped[Optional[list[int]]] = mapped_column(JSON, default=None)
    gefunden: Mapped[bool] = mapped_column(Boolean, default=False)
    antwort: Mapped[Optional[str]] = mapped_column(Text, default=None)
    fundstellen_json: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, default=None)
    # Warum eine Modellantwort verworfen wurde (Zitatpflicht) - leer, wenn nicht.
    verworfen_grund: Mapped[Optional[str]] = mapped_column(String(200), default=None)

    def __repr__(self) -> str:  # pragma: no cover - nur Diagnose
        return f"<MindAnfrage id={self.id} gefunden={self.gefunden}>"


def finde_dokument_nach_pfad(sitzung_: Session, pfad: str) -> WissensDokument | None:
    return sitzung_.scalar(select(WissensDokument).where(WissensDokument.pfad == pfad))


def loesche_chunks(sitzung_: Session, dokument_id: int) -> int:
    """Alle Chunks eines Dokuments entfernen (vor der Neuindexierung).

    Ohne diesen Schritt wuerde dieselbe Datei nach jeder Aenderung doppelt im
    Index stehen und Mind zweimal dieselbe Fundstelle anbieten.
    """
    ergebnis = sitzung_.execute(
        delete(WissensChunk).where(WissensChunk.dokument_id == dokument_id)
    )
    return ergebnis.rowcount or 0


# --- Lesezugriffe fuer die Oberflaeche --------------------------------------


def liste_tagesberichte(
    sitzung_: Session, *, projekt: str | None = None, limit: int = 100
) -> list[Tagesbericht]:
    """Berichte, neueste zuerst - optional auf ein Baulos eingegrenzt.

    Sortiert nach Berichtsdatum, nicht nach Eingang: Wird eine Sprachnachricht
    nachgereicht, gehoert der Bericht trotzdem an sein Datum.
    """
    abfrage = select(Tagesbericht)
    if projekt:
        abfrage = abfrage.where(Tagesbericht.projekt == projekt)
    abfrage = abfrage.order_by(
        Tagesbericht.datum.desc(),
        Tagesbericht.berichtsnummer.desc().nulls_last(),
        Tagesbericht.id.desc(),
    ).limit(limit)
    return list(sitzung_.scalars(abfrage).all())


def finde_bericht(sitzung_: Session, bericht_id: int) -> Tagesbericht | None:
    return sitzung_.get(Tagesbericht, bericht_id)


def bekannte_baulose(sitzung_: Session) -> list[str]:
    """Alle Baulose, zu denen es Berichte oder indexierte Unterlagen gibt.

    Bewusst aus den Daten und nicht aus einer Liste in der Konfiguration: Die
    Oberflaeche soll nur Baulose anbieten, zu denen wirklich etwas vorliegt.
    """
    aus_berichten = sitzung_.scalars(select(Tagesbericht.projekt).distinct()).all()
    aus_unterlagen = sitzung_.scalars(select(WissensDokument.projekt).distinct()).all()
    return sorted({wert for wert in (*aus_berichten, *aus_unterlagen) if wert})

"""Persistenz der Tagesberichte (SQLAlchemy).

Fuer den Durchstich reicht SQLite; ueber ``BAUTICS_DATABASE_URL`` laesst sich
ohne Codeaenderung auf Postgres umstellen.
"""

import datetime as dt
import logging
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Engine,
    Integer,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

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

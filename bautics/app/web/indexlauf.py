"""Zustand des laufenden Indexlaufs fuer die Oberflaeche.

Der Indexlauf dauert je nach Bestand Minuten - die Seite kann also nicht auf
ihn warten. Sie stoesst ihn an und zeigt danach zweierlei:

* die Kennzahlen aus der Datenbank (``mind.wissensbank_status``) - das ist
  die belastbare Wahrheit, sie ueberlebt jeden Neustart;
* die Bilanz des letzten Laufs in diesem Prozess - hilfreich, aber fluechtig.

TODO: Bei mehreren Arbeitsprozessen kennt jeder nur seine eigenen Laeufe.
Sobald das noetig wird, gehoert der Lauf in eine Tabelle (oder eine
Aufgabenschlange) statt in den Prozessspeicher.
"""

import datetime as dt
import logging
import threading
from dataclasses import dataclass
from typing import Optional

from .. import mind

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Laufzustand:
    laeuft: bool = False
    gestartet_am: Optional[dt.datetime] = None
    beendet_am: Optional[dt.datetime] = None
    bericht: Optional[mind.IndexBericht] = None
    abbruch: Optional[str] = None


_zustand = Laufzustand()
_sperre = threading.Lock()


def zustand() -> Laufzustand:
    with _sperre:
        return _zustand


def zuruecksetzen() -> None:
    """Nur fuer Tests - der Prozesszustand darf nicht zwischen Faellen lecken."""
    global _zustand
    with _sperre:
        _zustand = Laufzustand()


def beanspruche() -> bool:
    """Lauf fuer sich beanspruchen.

    True, wenn dieser Aufruf ihn startet; False, wenn schon einer laeuft. Wird
    absichtlich schon in der Route aufgerufen und nicht erst in der
    Hintergrundaufgabe: Sonst zeigt die Seite unmittelbar nach dem Klick noch
    "kein Lauf angestossen".
    """
    global _zustand
    with _sperre:
        if _zustand.laeuft:
            return False
        _zustand = Laufzustand(laeuft=True, gestartet_am=dt.datetime.now(dt.timezone.utc))
        return True


def _melde_ende(bericht: Optional[mind.IndexBericht], abbruch: Optional[str]) -> None:
    global _zustand
    with _sperre:
        _zustand = Laufzustand(
            laeuft=False,
            gestartet_am=_zustand.gestartet_am,
            beendet_am=dt.datetime.now(dt.timezone.utc),
            bericht=bericht,
            abbruch=abbruch,
        )


def fuehre_aus() -> None:
    """Den beanspruchten Lauf durchfuehren - als Hintergrundaufgabe.

    Ausnahmen bleiben hier und landen als Abbruchgrund im Zustand, statt den
    Server weiter zu beschaeftigen. Der Grund selbst ist Technikersprache und
    geht nur ins Protokoll, nicht auf die Seite.
    """
    try:
        bericht = mind.indexiere_wissensbank()
    except Exception as fehler:  # noqa: BLE001 - Grund gehoert auf die Seite
        logger.exception("Indexlauf abgebrochen.")
        _melde_ende(None, type(fehler).__name__)
        return
    _melde_ende(bericht, None)

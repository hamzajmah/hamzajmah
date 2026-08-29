"""Die zehn Agenten der Bautics Suite fuer die Seitenleiste.

Gebaut sind bisher Echo und Mind. Die uebrigen acht stehen sichtbar als
"bald verfuegbar" in der Leiste: Sie erzaehlen dem Kunden die Produktvision,
duerfen aber nichts vortaeuschen - deshalb ohne Verweis und nicht anklickbar.
Produktnamen bleiben englisch (CLAUDE.md).
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Agent:
    name: str
    kuerzel: str
    aufgabe: str
    # Kein Pfad => noch nicht gebaut, erscheint als "bald verfuegbar".
    pfad: Optional[str] = None


AGENTEN: tuple[Agent, ...] = (
    Agent("Echo", "EC", "Tagesberichte", "/echo"),
    Agent("Mind", "MI", "Projektwissen", "/mind"),
    Agent("Scribe", "SR", "Protokolle & Fotos"),
    Agent("Pulse", "PU", "Monatsbericht"),
    Agent("Claim", "CL", "Nachträge"),
    Agent("Track", "TK", "Termine"),
    Agent("Scan", "SN", "LV & Massen"),
    Agent("Radar", "RA", "Ausschreibungen"),
    Agent("Bid", "BD", "Angebote"),
    Agent("Delta", "DL", "Revisionen"),
)

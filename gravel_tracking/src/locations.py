"""Ortsangaben aus dem Notizfeld des Wareneingangs.

Die Bauleitung notiert im IFS Feld Notes, wohin die Fuhre ging. Drei Formen
kommen vor:

    "SP37 LS 10044"        -> ein Setzpunkt
    "SP122 - SP131"        -> eine Spanne ueber mehrere Setzpunkte
    "Q249 LS 2543536083"   -> ein Querungsbauwerk

Eine Spanne wird nicht stillschweigend auf die einzelnen Punkte verteilt. Sie
bleibt als Spanne erhalten; eine Verteilung ist ein eigener, gekennzeichneter
Rechenschritt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

POINT = "point"
SPAN = "span"
CROSSING = "crossing"
NONE = "none"

# Kein \b vor SP und Q: im Bestand stehen Tippfehler wie "2SP132" und "0Q249".
# Ein Buchstabe davor schliesst den Treffer dagegen aus, sonst wuerde "ASP" passen.
# "SP 48 - SP 51", "SP122-SP131", "SP34 – SP47"
_SPAN = re.compile(r"(?<![A-Za-z])SP\s*(\d{1,3})[a-z]?\s*[-–—]\s*(?:SP\s*)?(\d{1,3})[a-z]?", re.IGNORECASE)
# "SP142a" ist ein eigener Punkt, der Buchstabe gehoert zur Bezeichnung.
_POINT = re.compile(r"(?<![A-Za-z])SP\s*(\d{1,3})([a-z])?", re.IGNORECASE)
_CROSSING = re.compile(r"(?<![A-Za-z])Q\s*R?\s*[-–]?\s*(\d{2,4})", re.IGNORECASE)
# Lieferscheinnummern nicht mit Ortsnummern verwechseln.
_LS = re.compile(r"\bLS\s*[:.]?\s*\d+", re.IGNORECASE)


@dataclass(frozen=True)
class Location:
    location_type: str
    location_label: str
    location_from: int | None = None
    location_to: int | None = None
    span_count: int = 0

    @property
    def points(self) -> list[str]:
        """Alle Setzpunkte, die diese Angabe beruehrt."""
        if self.location_type == POINT and self.location_from is not None:
            return [point_label(self.location_from)]
        if self.location_type == SPAN and self.location_from is not None and self.location_to is not None:
            return [point_label(n) for n in range(self.location_from, self.location_to + 1)]
        if self.location_type == CROSSING:
            return [self.location_label]
        return []


EMPTY = Location(location_type=NONE, location_label="")


def point_label(number: int, suffix: str = "") -> str:
    """Fuehrende Nullen, damit SP47 vor SP132 sortiert."""
    return f"SP{number:03d}{suffix.lower()}"


def parse(note: str) -> Location:
    text = (note or "").strip()
    if not text:
        return EMPTY

    # Lieferscheinnummern entfernen, sonst liest der Querungs-Regex sie mit.
    cleaned = _LS.sub(" ", text)

    span = _SPAN.search(cleaned)
    if span:
        first, second = int(span.group(1)), int(span.group(2))
        low, high = min(first, second), max(first, second)
        return Location(
            location_type=SPAN,
            location_label=f"{point_label(low)}-{point_label(high)}",
            location_from=low,
            location_to=high,
            span_count=high - low + 1,
        )

    point = _POINT.search(cleaned)
    if point:
        number = int(point.group(1))
        suffix = point.group(2) or ""
        return Location(
            location_type=POINT,
            location_label=point_label(number, suffix),
            location_from=number,
            location_to=number,
            span_count=1,
        )

    crossing = _CROSSING.search(cleaned)
    if crossing:
        number = int(crossing.group(1))
        return Location(location_type=CROSSING, location_label=f"QR - {number}", location_from=number, location_to=number, span_count=1)

    return EMPTY

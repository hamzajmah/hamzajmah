"""Bereichsaufloesung.

Primaerquelle ist der Ordner (PDF) bzw. das IFS Sub Project (ERP), weil die
Sortierung fachlich erfolgt ist. Der Dokumentinhalt prueft gegen, ueberschreibt
aber nie. Bei Widerspruch gewinnt der Ordner, der Konflikt geht in die
Entscheidungswarteschlange.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

AREA_UNASSIGNED = "GENERAL"


@dataclass(frozen=True)
class AreaResolution:
    area_from_folder: str
    area_from_document: str
    area_final: str
    area_class: str
    area_conflict: bool


def classify_area(area_id: str, patterns: dict[str, str]) -> str:
    for name, pattern in patterns.items():
        if area_id and re.match(pattern, area_id):
            return name
    return "unknown"


def resolve(area_from_folder: str, area_from_document: str, patterns: dict[str, str]) -> AreaResolution:
    folder = (area_from_folder or "").strip()
    doc = (area_from_document or "").strip()
    conflict = bool(folder and doc and folder != doc)
    final = folder or doc or AREA_UNASSIGNED
    return AreaResolution(
        area_from_folder=folder,
        area_from_document=doc,
        area_final=final,
        area_class=classify_area(final, patterns),
        area_conflict=conflict,
    )

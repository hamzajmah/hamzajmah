"""Leser fuer die bestehende Tracking Arbeitsmappe.

Die Mappe enthaelt zwei Dinge, die aus den Rohquellen allein nicht zu holen
sind:

1. die Wareneingaenge der Bestellung P100012091, fuer die kein eigener Export
   vorliegt
2. eine Ortszuordnung, die aus den Originalbelegen erarbeitet wurde und damit
   dort greift, wo das Notizfeld des Wareneingangs nur eine Spanne kennt

Der Schluessel `P100042563|1|1|1` ist Bestellung, Bestellposition, Zeile und
Wareneingangsnummer. Er passt genau auf die Zeilen des ERP Exports.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .locations import CROSSING, NONE, POINT, point_label

# "SP47" und "SP047" sind derselbe Punkt, "Q390" ist "QR - 390".
_SP = re.compile(r"^SP\s*(\d{1,3})([a-z])?$", re.IGNORECASE)
_Q = re.compile(r"^Q\s*R?\s*-?\s*(\d{1,4})$", re.IGNORECASE)
BE_AREA = "be_area"


@dataclass(frozen=True)
class TrackingRow:
    key: str
    order_no: str
    order_line: str
    receipt_no: str
    delivery_date: date | None
    material_group: str
    grain_size: str
    quantity_t: float | None
    location_label: str
    location_type: str
    location_number: int | None
    section_key: str
    structure_name: str
    km_from: float | None
    km_to: float | None
    construction_method: str
    location_source: str
    location_resolution: str
    location_confidence: float
    delivery_note_no: str
    invoice_no: str
    source_row: int

    @property
    def material_class(self) -> str:
        return "sand" if self.material_group.lower().startswith("sand") else "mineral_mixture"


def normalize_location(code: str, kind: str) -> tuple[str, str, int | None]:
    """Ortscode der Mappe auf die Schreibweise der Pipeline bringen."""
    text = (code or "").strip()
    if not text or text.upper() == "UNGEKLAERT":
        return "", NONE, None
    m = _SP.match(text)
    if m:
        number = int(m.group(1))
        return point_label(number, m.group(2) or ""), POINT, number
    m = _Q.match(text)
    if m:
        number = int(m.group(1))
        return f"QR - {number}", CROSSING, number
    # BE Flaechen tragen einen sprechenden Namen, keinen Nummerncode.
    return text, BE_AREA if "be" in (kind or "").lower() else NONE, None


def _text(value: Any) -> str:
    if value is None or value != value:
        return ""
    return str(value).strip()


def _number(value: Any) -> float | None:
    if value is None or value != value or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def read_tracking(path: Path, sheet: str) -> list[TrackingRow]:
    import pandas as pd

    frame = pd.read_excel(path, sheet_name=sheet)
    required = ["erp_schluessel", "datum", "menge_t", "koernung", "materialgruppe", "ortscode"]
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(f"Tracking Mappe: Spalten fehlen im Blatt '{sheet}': {missing}")

    rows: list[TrackingRow] = []
    for index, row in enumerate(frame.to_dict("records"), start=2):
        key = _text(row.get("erp_schluessel"))
        if not key:
            continue
        parts = key.split("|")
        label, kind, number = normalize_location(_text(row.get("ortscode")), _text(row.get("ortstyp")))
        section = _text(row.get("sektion"))
        rows.append(
            TrackingRow(
                key=key,
                order_no=parts[0] if parts else "",
                order_line=parts[1] if len(parts) > 1 else "",
                receipt_no=parts[3] if len(parts) > 3 else "",
                delivery_date=_day(row.get("datum")),
                material_group=_text(row.get("materialgruppe")),
                grain_size=_text(row.get("koernung")),
                quantity_t=_number(row.get("menge_t")),
                location_label=label,
                location_type=kind,
                location_number=number,
                section_key=f"AS{section}" if section else "",
                structure_name=_text(row.get("bauwerksflaeche")),
                km_from=_number(row.get("km_von")),
                km_to=_number(row.get("km_bis")),
                construction_method=_text(row.get("bauweise")),
                location_source=_text(row.get("ortsquelle"))[:120],
                location_resolution=_text(row.get("ortsaufloesung")),
                location_confidence=_number(row.get("ortskonfidenz")) or 0.0,
                delivery_note_no=_text(row.get("lieferschein_nr")),
                invoice_no=_text(row.get("rechnung_nr")),
                source_row=index,
            )
        )
    return rows

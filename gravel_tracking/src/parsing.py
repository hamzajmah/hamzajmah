"""Deterministisches Parsing von Lieferschein-Rohtext ueber Lieferantenvorlagen.

Reine Funktionen ohne Dateizugriff, damit sie gegen Rohtextfixtures testbar
sind. Es wird nie geraten: ein Feld, das die Vorlage nicht findet, bleibt leer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

PAGE_MARKER = re.compile(r"-----\s*PAGE\s+(\d+)\s*-----")


def normalize_number(raw: str) -> float | None:
    """'1.234,56' und '1234.56' -> 1234.56. Nicht deutbar -> None."""
    if raw is None:
        return None
    text = raw.strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def parse_date_de(raw: str) -> date | None:
    m = re.match(r"^\s*(\d{1,2})[.](\d{1,2})[.](\d{2,4})\s*$", raw or "")
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


@dataclass
class ParsedNote:
    page: int
    delivery_note_no: str = ""
    delivery_date: date | None = None
    quantity_t: float | None = None
    quantity_m3_doc: float | None = None
    order_no: str = ""
    vehicle_id: str = ""
    material_text: str = ""
    confidence: float = 0.0
    fields_found: list[str] = field(default_factory=list)


def split_pages(text: str) -> list[tuple[int, str]]:
    parts = PAGE_MARKER.split(text)
    if len(parts) == 1:
        return [(1, text)]
    pages = []
    for i in range(1, len(parts), 2):
        pages.append((int(parts[i]), parts[i + 1]))
    return pages


def _search(pattern: str, text: str) -> str:
    if not pattern:
        return ""
    m = re.search(pattern, text, flags=re.IGNORECASE)
    if not m:
        return ""
    return (m.group(1) if m.groups() else m.group(0)).strip()


# Gewichte der Felder fuer die Konfidenz. Summe = 1.0 bei vollstaendigem Treffer.
_WEIGHTS = {"delivery_note_no": 0.30, "delivery_date": 0.25, "quantity_t": 0.30, "material_text": 0.15}


def parse_page(page_no: int, text: str, template: dict[str, Any]) -> ParsedNote:
    pat = template.get("patterns", {})
    note = ParsedNote(page=page_no)
    note.delivery_note_no = _search(pat.get("delivery_note_no", ""), text)
    raw_date = _search(pat.get("delivery_date", ""), text)
    if raw_date:
        note.delivery_date = parse_date_de(raw_date) or parse_date_de(_first_full_date(text))
    else:
        note.delivery_date = parse_date_de(_first_full_date(text))
    note.quantity_t = normalize_number(_search(pat.get("quantity_t", ""), text))
    note.quantity_m3_doc = normalize_number(_search(pat.get("quantity_m3", ""), text))
    note.order_no = _search(pat.get("order_no", ""), text)
    note.vehicle_id = _search(pat.get("vehicle_id", ""), text)
    note.material_text = _search(pat.get("material_text", ""), text)

    score = 0.0
    for name, weight in _WEIGHTS.items():
        value = getattr(note, name)
        if value not in (None, ""):
            score += weight
            note.fields_found.append(name)
    note.confidence = round(score, 2)
    return note


def _first_full_date(text: str) -> str:
    m = re.search(r"\b(\d{1,2}[.]\d{1,2}[.]\d{2,4})\b", text or "")
    return m.group(1) if m else ""


def parse_document(text: str, template: dict[str, Any]) -> list[ParsedNote]:
    return [parse_page(no, page_text, template) for no, page_text in split_pages(text) if page_text.strip()]


def detect_supplier(text: str, templates: list[dict[str, Any]]) -> dict[str, Any] | None:
    low = (text or "").lower()
    for tpl in templates:
        if any(a.lower() in low for a in tpl.get("anchors", [])):
            return tpl
    return None

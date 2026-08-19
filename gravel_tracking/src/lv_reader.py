"""Leser fuer die Leistungsmeldung (Hauptvertrag und Nachtraege).

Aufbau der Datei: ueber der eigentlichen Tabelle stehen Kopfangaben, die
Spaltenueberschriften folgen erst weiter unten. Zeilen ohne Eintrag in der
Spalte KT sind Gliederungsebenen, Zeilen mit KT sind LV Positionen.

Aus dem Gliederungspfad ergibt sich der Bereich:
    Kapitel 4.x  -> "Abschnitt 04S-3-07"      -> IFS Sub Project AS04S-3-07
    Kapitel 3.x.y-> "Querungsbauwerk Nr. 174" -> IFS Sub Project QR - 174
Damit ist die Bereichszuordnung der LV Seite belegt und nicht geraten.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

SECTION_PATTERN = re.compile(r"Abschnitt\s+(04S-3-\d+)", re.IGNORECASE)
CROSSING_PATTERN = re.compile(r"Querungsbauwerk\s*Nr[.:]?\s*(\d+)", re.IGNORECASE)
WEEK_PATTERN = re.compile(r"^(?:Bis\s+)?KW\s*(\d{1,2})/(\d{2})$", re.IGNORECASE)


@dataclass
class LvPosition:
    lv_position_no: str
    lv_source: str
    group_path: str
    short_text: str
    unit: str
    contract_quantity: float | None
    billed_quantity: float | None
    unit_price_eur: float | None
    contract_value_eur: float | None
    billed_value_eur: float | None
    area_key: str
    material_group: str = ""
    is_gravel_relevant: bool = False
    timeline_matches_total: bool = True
    monthly: dict[str, float] = field(default_factory=dict)

    @property
    def progress_pct(self) -> float | None:
        if not self.contract_quantity:
            return None
        if self.billed_quantity is None:
            return None
        return round(100.0 * self.billed_quantity / self.contract_quantity, 2)


def _week_to_month(label: str) -> str:
    """'KW 32/25' -> '2025-08'. Der Monat des Wochenmontags entscheidet."""
    m = WEEK_PATTERN.match(str(label).strip())
    if not m:
        return ""
    week, year_short = int(m.group(1)), int(m.group(2))
    monday = date.fromisocalendar(2000 + year_short, week, 1)
    return monday.strftime("%Y-%m")


def _num(value: Any) -> float | None:
    if value is None or value != value or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _area_from_path(path: str) -> str:
    m = SECTION_PATTERN.search(path)
    if m:
        return f"AS{m.group(1)}"
    m = CROSSING_PATTERN.search(path)
    if m:
        return f"QR - {m.group(1)}"
    return ""


def _depth(oz: str) -> int:
    return str(oz).count(".")


def read_sheet(path: Path, sheet: str, header_row: int, lv_source: str) -> list[LvPosition]:
    """header_row ist die 1-basierte Zeile mit den Spaltenueberschriften."""
    import pandas as pd

    frame = pd.read_excel(path, sheet_name=sheet, header=header_row - 1)
    columns = {str(c): c for c in frame.columns}
    required = ["OZ", "KT", "KURZTEXT", "LV-MENGE", "EINHEIT", "EP", "GP", "RE MENGE"]
    missing = [c for c in required if c not in columns]
    if missing:
        raise ValueError(f"Leistungsmeldung: Spalten fehlen in '{sheet}': {missing}")

    # Der Leistungswert je Position steht in der zweiten GP Spalte.
    gp_columns = [str(c) for c in frame.columns if str(c).startswith("GP")]
    billed_value_col = gp_columns[1] if len(gp_columns) > 1 else None
    week_columns = [str(c) for c in frame.columns if WEEK_PATTERN.match(str(c))]

    positions: list[LvPosition] = []
    breadcrumb: dict[int, str] = {}

    for row in frame.to_dict("records"):
        oz = row.get("OZ")
        if oz is None or oz != oz or str(oz).strip() == "":
            continue
        oz = str(oz).strip()
        text = "" if row.get("KURZTEXT") != row.get("KURZTEXT") else str(row.get("KURZTEXT") or "").strip()
        kt = row.get("KT")
        is_group = kt is None or kt != kt or str(kt).strip() == ""

        if is_group:
            level = _depth(oz)
            breadcrumb[level] = text
            for deeper in [k for k in breadcrumb if k > level]:
                breadcrumb.pop(deeper)
            continue

        group_path = " > ".join(breadcrumb[k] for k in sorted(breadcrumb))
        price = _num(row.get("EP"))
        monthly: dict[str, float] = {}
        timeline_total = 0.0
        for column in week_columns:
            value = _num(row.get(column))
            if not value or not price:
                continue
            quantity = value / price
            month = _week_to_month(column)
            if month:
                monthly[month] = round(monthly.get(month, 0.0) + quantity, 4)
                timeline_total += quantity

        billed = _num(row.get("RE MENGE"))
        matches = billed is None or abs(timeline_total - billed) <= max(0.5, abs(billed) * 0.001)

        positions.append(
            LvPosition(
                lv_position_no=oz,
                lv_source=str(kt).strip() or lv_source,
                group_path=group_path,
                short_text=text,
                unit=str(row.get("EINHEIT") or "").strip(),
                contract_quantity=_num(row.get("LV-MENGE")),
                billed_quantity=billed,
                unit_price_eur=price,
                contract_value_eur=_num(row.get("GP")),
                billed_value_eur=_num(row.get(billed_value_col)) if billed_value_col else None,
                area_key=_area_from_path(group_path),
                timeline_matches_total=matches,
                monthly=dict(sorted(monthly.items())),
            )
        )
    return positions


def read_leistungsmeldung(path: Path, sheets: list[dict[str, Any]]) -> list[LvPosition]:
    positions: list[LvPosition] = []
    for spec in sheets:
        positions.extend(read_sheet(path, spec["name"], int(spec["header_row"]), spec.get("lv_source", "")))
    return positions


def apply_mapping(positions: list[LvPosition], mapping: dict[str, Any]) -> None:
    """Ordnet LV Positionen einer Materialgruppe zu.

    Die Zuordnung steht in config/lv_mapping.yaml und ist ein Vorschlag, der
    fachlich freizugeben ist. Nichts wird geraten: was auf keine Regel passt,
    bleibt ohne Materialgruppe und damit ausserhalb des Vergleichs.
    """
    rules = mapping.get("groups", {})
    for position in positions:
        for group_name, rule in rules.items():
            patterns = rule.get("match_short_text", [])
            if any(re.search(p, position.short_text, re.IGNORECASE) for p in patterns):
                exclude = rule.get("exclude_short_text", [])
                if any(re.search(p, position.short_text, re.IGNORECASE) for p in exclude):
                    continue
                position.material_group = group_name
                position.is_gravel_relevant = bool(rule.get("consumes_delivered_material", False))
                break

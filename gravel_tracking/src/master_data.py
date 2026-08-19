"""Stammdaten der Trasse.

Quelle ist die Uebersicht der Bauwerksflaechen mit Sektion, Kilometrierung,
Bauweise, Querungsnummer und Zufahrten. Sie liefert drei Dinge, die keine
Buchung hergibt:

* zu welcher Sektion ein Querungsbauwerk gehoert
* wo ein Bauwerk auf der Trasse liegt (Kilometrierung)
* mit welcher Bauweise gearbeitet wird (offen, HDD, Microtunnel, Pressbohr, EPP)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CROSSING_NUMBER = re.compile(r"Q\s*(\d{2,4})", re.IGNORECASE)

OPEN_METHODS = {"OBW"}


@dataclass(frozen=True)
class Structure:
    sequence: int | None
    section: str            # "04S-3-01"
    section_key: str        # "AS04S-3-01", wie im ERP
    structure_name: str     # Bauwerksflaeche, z.B. "H-C2-31-011-V0"
    km_from: float | None
    km_to: float | None
    method: str             # OBW, GBW-HDD, GWB-Microtunnel, ...
    crossing_no: str        # "Q114" oder leer
    area_key: str           # "QR - 114" fuer Querungen, sonst der Sektionsschluessel
    access_roads: str

    @property
    def km_mid(self) -> float | None:
        if self.km_from is None or self.km_to is None:
            return None
        return round((self.km_from + self.km_to) / 2.0, 1)

    @property
    def length_m(self) -> float | None:
        if self.km_from is None or self.km_to is None:
            return None
        return round(self.km_to - self.km_from, 1)

    @property
    def is_crossing(self) -> bool:
        return bool(self.crossing_no)


def _text(value: object) -> str:
    if value is None or value != value:
        return ""
    return str(value).strip()


def _number(value: object) -> float | None:
    if value is None or value != value or value == "":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def read_structures(path: Path, sheet: str, header_row: int) -> list[Structure]:
    import pandas as pd

    frame = pd.read_excel(path, sheet_name=sheet, header=header_row - 1)
    columns = {str(c).strip(): c for c in frame.columns}
    required = ["Sektion", "Bauwerksfläche", "KM von", "KM bis", "Bauweise"]
    missing = [c for c in required if c not in columns]
    if missing:
        raise ValueError(f"Stammdaten Bauwerke: Spalten fehlen: {missing}")

    structures: list[Structure] = []
    for row in frame.to_dict("records"):
        section = _text(row.get(columns["Sektion"]))
        if not section:
            continue
        crossing_raw = _text(row.get(columns.get("Querungsnummer", "Querungsnummer")))
        match = CROSSING_NUMBER.search(crossing_raw)
        crossing_no = f"Q{match.group(1)}" if match else ""
        section_key = f"AS{section}"
        structures.append(
            Structure(
                sequence=int(_number(row.get(columns.get("Lfd- Nr.", "Lfd- Nr."))) or 0) or None,
                section=section,
                section_key=section_key,
                structure_name=_text(row.get(columns["Bauwerksfläche"])),
                km_from=_number(row.get(columns["KM von"])),
                km_to=_number(row.get(columns["KM bis"])),
                method=_text(row.get(columns["Bauweise"])),
                crossing_no=crossing_no,
                area_key=f"QR - {match.group(1)}" if match else section_key,
                access_roads=_text(row.get(columns.get("Zufartsnummern", "Zufartsnummern"))),
            )
        )
    return structures


def section_extent(structures: list[Structure]) -> dict[str, tuple[float, float]]:
    """Kilometrierung je Sektion, aus den zugehoerigen Bauwerksflaechen."""
    extent: dict[str, tuple[float, float]] = {}
    for item in structures:
        if item.km_from is None or item.km_to is None:
            continue
        low, high = extent.get(item.section_key, (item.km_from, item.km_to))
        extent[item.section_key] = (min(low, item.km_from), max(high, item.km_to))
    return extent

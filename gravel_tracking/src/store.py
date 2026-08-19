"""Persistenter Satzspeicher.

Saetze werden ueber record_id identifiziert. Ein zweiter Lauf ueber denselben
Bestand erzeugt dieselben IDs und damit keine Duplikate (Idempotenz).
"""
from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from .validators import RECORD_COLUMNS, DeliveryRecord


def _to_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _from_row(row: dict[str, str]) -> DeliveryRecord:
    data: dict[str, object] = {}
    for key, value in row.items():
        if key not in RECORD_COLUMNS:
            continue
        if value == "":
            data[key] = None if key not in ("record_id", "source_system", "source_file", "doc_type", "supplier_name", "material_text") else ""
            continue
        data[key] = value
    data = {k: v for k, v in data.items() if v is not None or k in ("delivery_date",)}
    return DeliveryRecord(**data)  # type: ignore[arg-type]


class RecordStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._records: dict[str, DeliveryRecord] = {}
        if path.exists():
            self.load()

    def load(self) -> None:
        with self.path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                rec = _from_row(row)
                self._records[rec.record_id] = rec

    def upsert(self, records: Iterable[DeliveryRecord]) -> int:
        added = 0
        for rec in records:
            if rec.record_id not in self._records:
                added += 1
            self._records[rec.record_id] = rec
        return added

    def drop_source(self, source_file: str) -> None:
        """Entfernt alle Saetze einer Quelle, bevor sie neu eingelesen wird."""
        for rid in [r.record_id for r in self._records.values() if r.source_file == source_file]:
            del self._records[rid]

    def records(self) -> list[DeliveryRecord]:
        return sorted(
            self._records.values(),
            key=lambda r: (
                r.delivery_date.isoformat() if r.delivery_date else "",
                r.source_file,
                r.record_id,
            ),
        )

    def __len__(self) -> int:
        return len(self._records)

    def total_t(self, charge_type: str = "material_supply") -> float:
        return round(
            sum(r.quantity_t or 0.0 for r in self._records.values() if r.charge_type == charge_type), 2
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, delimiter=";", lineterminator="\n")
            writer.writerow(RECORD_COLUMNS)
            for rec in self.records():
                writer.writerow([_to_cell(getattr(rec, col)) for col in RECORD_COLUMNS])
        tmp.replace(self.path)

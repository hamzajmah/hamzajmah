"""Pydantic Schema und maschinelle Abnahmeregeln.

Ob ein Task fertig ist, entscheidet nicht das Modell, sondern ein
deterministischer Check (Abschnitt 4 des Auftragsprompts).
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Spaltenreihenfolge der Ausgabe. Feste Reihenfolge = deterministische Dateien.
RECORD_COLUMNS = [
    "record_id",
    "source_system",
    "source_file",
    "source_page",
    "source_row_ref",
    "doc_type",
    "supplier_name",
    "delivery_note_no",
    "delivery_note_source",
    "invoice_no",
    "order_no",
    "delivery_date",
    "material_text",
    "material_class",
    "grain_size",
    "rock_type",
    "transport_class",
    "charge_type",
    "quantity",
    "unit",
    "quantity_t",
    "quantity_m3_doc",
    "delivered_m3_loose",
    "delivered_m3_installed",
    "delivered_m3_installed_low",
    "delivered_m3_installed_high",
    "conversion_source",
    "conversion_confidence",
    "price_per_unit",
    "amount_eur",
    "unload_location_text",
    "location_type",
    "location_label",
    "location_from",
    "location_to",
    "location_span_count",
    "area_from_folder",
    "area_from_document",
    "area_final",
    "area_class",
    "area_conflict",
    "activity_id",
    "activity_text",
    "vehicle_id",
    "extraction_method",
    "extraction_confidence",
    "is_duplicate",
    "dedup_key",
    "needs_review",
    "review_reason",
]

REQUIRED_FIELDS = ("delivery_note_no", "delivery_date", "quantity", "unit", "material_text")


class DeliveryRecord(BaseModel):
    """Eine Zeile je Lieferschein bzw. Rechnungs-/Wareneingangsposition."""

    model_config = ConfigDict(extra="forbid")

    record_id: str
    source_system: str
    source_file: str
    source_page: int | None = None
    source_row_ref: str = ""
    doc_type: str
    supplier_name: str
    delivery_note_no: str = ""
    delivery_note_source: str = ""
    invoice_no: str = ""
    order_no: str = ""
    delivery_date: date | None = None
    material_text: str
    material_class: str = ""
    grain_size: str = ""
    rock_type: str = ""
    transport_class: str = ""
    charge_type: str = ""
    quantity: float | None = None
    unit: str = ""
    quantity_t: float | None = None
    quantity_m3_doc: float | None = None
    delivered_m3_loose: float | None = None
    delivered_m3_installed: float | None = None
    delivered_m3_installed_low: float | None = None
    delivered_m3_installed_high: float | None = None
    conversion_source: str = ""
    conversion_confidence: str = "none"
    price_per_unit: float | None = None
    amount_eur: float | None = None
    unload_location_text: str = ""
    location_type: str = "none"
    location_label: str = ""
    location_from: int | None = None
    location_to: int | None = None
    location_span_count: int = 0
    area_from_folder: str = ""
    area_from_document: str = ""
    area_final: str = ""
    area_class: str = ""
    area_conflict: bool = False
    activity_id: str = ""
    activity_text: str = ""
    vehicle_id: str = ""
    extraction_method: str = "template"
    extraction_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    is_duplicate: bool = False
    dedup_key: str = ""
    needs_review: bool = False
    review_reason: str = ""

    def material_key(self) -> str:
        """Schluessel aus Klasse und Koernung, z.B. 'mineral_mixture 0/8'."""
        return f"{self.material_class} {self.grain_size}".strip()

    @field_validator("quantity", "quantity_t", "quantity_m3_doc")
    @classmethod
    def _non_negative(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("Menge darf nicht negativ sein")
        return v


def make_record_id(*parts: Any) -> str:
    """Deterministische Satz-ID. Gleiche Quelle -> gleiche ID -> idempotent."""
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def check_record(record: DeliveryRecord, cfg: dict[str, Any]) -> list[str]:
    """Maschinelle Abnahme eines Extraktionsergebnisses.

    Gibt die Liste der Verstoesse zurueck. Leere Liste = bestanden.
    """
    problems: list[str] = []
    for field in REQUIRED_FIELDS:
        value = getattr(record, field)
        if value in (None, "", []):
            problems.append(f"pflichtfeld_fehlt:{field}")

    min_conf = float(cfg["extraction"]["min_confidence"])
    if record.extraction_confidence < min_conf:
        problems.append(f"confidence_unter_schwelle:{record.extraction_confidence:.2f}")

    problems.extend(plausibility_problems(record, cfg))
    return problems


def plausibility_problems(record: DeliveryRecord, cfg: dict[str, Any]) -> list[str]:
    """Plausibilitaetsregeln aus Phase 3. Jeder Verstoss setzt needs_review."""
    problems: list[str] = []
    pl = cfg["plausibility"]
    period_start = date.fromisoformat(cfg["project"]["period_start"])
    period_end = date.fromisoformat(cfg["project"]["period_end"])

    if record.charge_type == "material_supply" and record.quantity_t is not None and not (
        float(pl["quantity_t_min"]) <= record.quantity_t <= float(pl["quantity_t_max"])
    ):
        problems.append(f"menge_ausserhalb_bandbreite:{record.quantity_t}")
    if record.delivery_date is not None:
        if record.delivery_date < period_start or record.delivery_date > period_end:
            problems.append(f"datum_ausserhalb_projektzeitraum:{record.delivery_date.isoformat()}")
        if record.delivery_date > date.today():
            problems.append("datum_in_der_zukunft")
    if record.charge_type == "material_supply" and not record.grain_size:
        problems.append("koernung_nicht_erkannt")
    return problems

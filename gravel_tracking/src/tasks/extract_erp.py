"""T2 Strukturierte Extraktion aus dem IFS Wareneingangsexport.

Der Export ist bereits strukturiert, deshalb entfaellt die OCR Eskalation.
Die fachliche Arbeit steckt in der Klassifikation der Positionsarten:

* material_supply       -> Schotterlieferung, zaehlt als Liefermenge
* disposal_acceptance   -> Annahme Erdaushub/Bohrklein, Gegenrichtung, eigene Kennzahl
* surcharge / freight   -> Diesel-, Samstags-, Frachtzuschlaege. Diese Zeilen tragen
                           Tonnen nur als Abrechnungsbasis und duerfen niemals in die
                           Liefermenge einfliessen, sonst wird dieselbe Fuhre mehrfach
                           gezaehlt.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from ..areas import resolve
from ..conversion import convert
from ..decisions import Decision
from ..harness import Context, SchemaChangeRequired, TaskResult
from ..locations import parse as parse_location
from ..materials import CHARGE_SUPPLY, classify, factor_key
from ..state import Task
from ..validators import DeliveryRecord, make_record_id, plausibility_problems

REQUIRED_COLUMNS = [
    "Source Ref 1",
    "Source Ref 2",
    "Receipt No",
    "Sender Description",
    "Source Part Description",
    "Arrived Source Qty",
    "Measure Unit",
    "Actual Delivery Date",
    "Sub Project ID",
]

LS_PATTERN = re.compile(r"\bLS\s*[:.]?\s*([0-9]{3,})", re.IGNORECASE)


def _as_date(value: Any) -> date | None:
    if value is None or value != value:  # NaN
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def _as_str(value: Any) -> str:
    if value is None or value != value:
        return ""
    return str(value).strip()


def _as_float(value: Any) -> float | None:
    if value is None or value != value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _de(value: float, digits: int = 0) -> str:
    """Zahl in deutscher Schreibweise, Punkt als Tausender-, Komma als Dezimaltrenner."""
    return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def run(task: Task, ctx: Context) -> TaskResult:
    import pandas as pd

    path = ctx.cfg.root / task.source_file
    frame = pd.read_excel(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise SchemaChangeRequired(f"Spalten fehlen im ERP Export {task.source_file}: {missing}")

    since = ctx.since
    area_patterns = ctx.cfg["areas"]["classes"]
    factors = ctx.factors
    supplier_cfg = {s["erp_sender_match"]: s["name"] for s in ctx.cfg.get("suppliers", [])}

    records: list[DeliveryRecord] = []
    stats = {"rows": 0, "supply": 0, "disposal": 0, "surcharge": 0, "review": 0, "skipped_since": 0}

    for row in frame.to_dict("records"):
        stats["rows"] += 1
        delivery_date = _as_date(row.get("Actual Delivery Date"))
        if since and delivery_date and delivery_date.isoformat() < since:
            stats["skipped_since"] += 1
            continue

        material_text = _as_str(row.get("Source Part Description"))
        info = classify(material_text)
        unit = _as_str(row.get("Measure Unit"))
        quantity = _as_float(row.get("Arrived Source Qty"))

        # Nur echte Liefer- und Annahmemengen in Tonnen fuehren quantity_t.
        quantity_t = quantity if unit.lower() == "ton" and info.charge_type in ("material_supply", "disposal_acceptance") else None
        conv = convert(quantity_t if info.charge_type == CHARGE_SUPPLY else None, factor_key(info), factors)

        notes = _as_str(row.get("Notes"))
        ls_match = LS_PATTERN.search(notes)
        receipt_ref = _as_str(row.get("Receipt Reference"))
        receipt_no = _as_str(row.get("Receipt No"))
        if ls_match:
            note_no, note_source, confidence = ls_match.group(1), "document_note", 1.0
        elif receipt_ref:
            note_no, note_source, confidence = f"{receipt_ref}-{receipt_no}", "erp_receipt_reference", 0.90
        else:
            note_no, note_source, confidence = "", "", 0.60

        sender = _as_str(row.get("Sender Description"))
        supplier_name = next((name for key, name in supplier_cfg.items() if key.lower() in sender.lower()), sender)

        area_id = _as_str(row.get("Sub Project ID"))
        area = resolve(area_id, "", area_patterns)

        # Der Originaltext bleibt erhalten, die Struktur kommt daneben.
        location = notes
        place = parse_location(notes)

        rec = DeliveryRecord(
            record_id=make_record_id(
                "erp", task.source_file, row.get("Source Ref 1"), row.get("Source Ref 2"),
                row.get("Source Ref 3"), row.get("Receipt No"),
            ),
            source_system="erp_ifs",
            source_file=task.source_file,
            source_row_ref=f"po={_as_str(row.get('Source Ref 1'))}/{_as_str(row.get('Source Ref 2'))};receipt={receipt_no}",
            doc_type="erp_receipt",
            supplier_name=supplier_name,
            delivery_note_no=note_no,
            delivery_note_source=note_source,
            order_no=_as_str(row.get("Source Ref 1")),
            delivery_date=delivery_date,
            material_text=material_text,
            material_class=info.material_class,
            grain_size=info.grain_size,
            rock_type=info.rock_type,
            transport_class=info.transport_class,
            charge_type=info.charge_type,
            quantity=quantity,
            unit=unit,
            quantity_t=quantity_t,
            delivered_m3_loose=conv.m3_loose,
            delivered_m3_installed=conv.m3_installed,
            delivered_m3_installed_low=conv.m3_installed_low,
            delivered_m3_installed_high=conv.m3_installed_high,
            conversion_source=conv.source,
            conversion_confidence=conv.confidence,
            unload_location_text=location,
            location_type=place.location_type,
            location_label=place.location_label,
            location_from=place.location_from,
            location_to=place.location_to,
            location_span_count=place.span_count,
            area_from_folder=area.area_from_folder,
            area_from_document=area.area_from_document,
            area_final=area.area_final,
            area_class=area.area_class,
            area_conflict=area.area_conflict,
            activity_id=_as_str(row.get("Activity ID")),
            activity_text=_as_str(row.get("Activity Description")),
            extraction_method="erp_export",
            extraction_confidence=confidence,
        )

        problems = plausibility_problems(rec, ctx.cfg.raw)
        if not note_no:
            problems.append("pflichtfeld_fehlt:delivery_note_no")
        if rec.extraction_confidence < float(ctx.cfg["extraction"]["min_confidence"]):
            problems.append(f"confidence_unter_schwelle:{rec.extraction_confidence:.2f}")
        if rec.charge_type == CHARGE_SUPPLY and rec.delivered_m3_installed is None:
            problems.append("keine_umrechnung_moeglich")
        if problems:
            rec.needs_review = True
            rec.review_reason = ";".join(sorted(set(problems)))
            stats["review"] += 1

        if info.charge_type == CHARGE_SUPPLY:
            stats["supply"] += 1
        elif info.charge_type == "disposal_acceptance":
            stats["disposal"] += 1
        elif info.charge_type in ("surcharge", "freight"):
            stats["surcharge"] += 1

        records.append(rec)

    if not records:
        return TaskResult(ok=False, message="keine Zeilen gelesen", error_class="erp_empty", escalate=False)

    unknown = [r for r in records if r.charge_type == "unknown"]
    if unknown:
        sample = sorted({r.material_text for r in unknown})[:5]
        ctx.decisions.add(
            Decision(
                category=4,
                topic="Nicht klassifizierbare ERP Positionsarten",
                detail=f"{len(unknown)} Zeilen lassen sich weder als Lieferung, Annahme noch als Zuschlag einordnen. Beispiele: {sample}",
                impact="Diese Mengen fehlen in allen Auswertungen.",
                proposal="Positionsarten fachlich zuordnen und die Regeln in src/materials.py ergaenzen.",
                evidence=task.source_file,
            )
        )

    _check_receipt_sequence(task, ctx, frame)
    _add_source_decisions(task, ctx, records, stats)

    ctx.store.drop_source(task.source_file)
    ctx.store.upsert(records)
    ctx.log(
        f"ERP {task.source_file} zeilen={stats['rows']} lieferung={stats['supply']} "
        f"annahme={stats['disposal']} zuschlag={stats['surcharge']} pruefliste={stats['review']}"
    )
    return TaskResult(ok=True, message=f"{len(records)} Saetze aus ERP", data=stats)


def _check_receipt_sequence(task: Task, ctx: Context, frame: Any) -> None:
    """Ist der Nummernkreis der Wareneingaenge je Bestellposition geschlossen?

    Fehlende Nummern bedeuten nicht zwingend fehlende Lieferungen: stornierte
    Wareneingaenge fehlen im Export, weil er auf Status 'Received' gefiltert ist.
    Der Befund gehoert trotzdem sichtbar in die Entscheidungswarteschlange.
    """
    grouped = frame.groupby("Source Ref 2")["Receipt No"]
    missing_total = 0
    lines_with_gaps = 0
    late_start = []
    for line, series in grouped:
        numbers = {int(n) for n in series.dropna()}
        if not numbers:
            continue
        low, high = min(numbers), max(numbers)
        gaps = (high - low + 1) - len(numbers)
        if gaps:
            lines_with_gaps += 1
            missing_total += gaps
        if low > 1:
            late_start.append((line, low))

    if not missing_total and not late_start:
        return

    total_rows = len(frame)
    ctx.decisions.add(
        Decision(
            category=6,
            topic="Luecken im Nummernkreis der Wareneingaenge",
            detail=(
                f"In {lines_with_gaps} Bestellpositionen fehlen zusammen {missing_total} Wareneingangsnummern "
                f"({100 * missing_total / max(total_rows, 1):.1f} Prozent der Zeilen). "
                f"{len(late_start)} Positionen beginnen nicht bei Nummer 1, "
                f"z.B. {', '.join(f'Position {int(line)} ab Nummer {low}' for line, low in late_start[:4])}."
            ),
            impact=(
                "Entweder handelt es sich um stornierte Wareneingaenge, die der Statusfilter ausblendet, oder um "
                "Lieferungen vor dem Beginn des Exportzeitraums. Im zweiten Fall fehlt Menge."
            ),
            proposal=(
                "In IFS je Beispielposition pruefen, was hinter den fehlenden Nummern steht, und den Export bei Bedarf "
                "ohne Datumsfilter erneut ziehen."
            ),
            evidence=task.source_file,
        )
    )


def _add_source_decisions(task: Task, ctx: Context, records: list[DeliveryRecord], stats: dict[str, int]) -> None:
    supply = [r for r in records if r.charge_type == CHARGE_SUPPLY]
    total_t = sum(r.quantity_t or 0.0 for r in supply)
    general_t = sum(r.quantity_t or 0.0 for r in supply if r.area_class == "general")
    surcharge_t = sum(r.quantity or 0.0 for r in records if r.charge_type in ("surcharge", "freight") and r.unit.lower() == "ton")

    if total_t and general_t:
        ctx.decisions.add(
            Decision(
                category=3,
                topic="Liefermenge ohne bereichsscharfe Buchung",
                detail=(
                    f"{_de(general_t)} t von {_de(total_t)} t ({_de(100 * general_t / total_t, 1)} Prozent, Stand vor "
                    "Dublettenbereinigung) sind im ERP auf das Sub Project GENERAL gebucht und damit keinem Abschnitt "
                    "und keiner Querung zugeordnet."
                ),
                impact="Ein Vergleich gegen LV Positionen je Bereich ist fuer diesen Anteil nicht moeglich.",
                proposal=(
                    "Entweder Nachbuchung im IFS je Abschnitt, oder Zuordnung ueber die Ortsangaben im Feld Notes "
                    "(SP-/QR-Nummern) nach fachlicher Freigabe der Zuordnungstabelle SP-Nummer -> Abschnitt."
                ),
                evidence=f"{task.source_file}, Feld 'Sub Project ID' = GENERAL",
            )
        )

    if surcharge_t:
        ctx.decisions.add(
            Decision(
                category=6,
                topic="Zuschlagszeilen tragen Tonnen als Abrechnungsbasis",
                detail=(
                    f"{_de(surcharge_t)} t stehen auf Diesel-, Samstags- und Frachtzuschlagszeilen. Sie beziehen sich auf "
                    "bereits erfasste Fuhren."
                ),
                impact="Wuerden sie mitgezaehlt, waere die ausgewiesene Liefermenge um diesen Betrag zu hoch.",
                proposal="Bestaetigung, dass Zuschlagszeilen als charge_type=surcharge gefuehrt und aus der Liefermenge ausgeschlossen bleiben.",
                evidence=f"{task.source_file}, Positionsarten mit 'zuschlag' bzw. 'Frachtkosten'",
            )
        )

    disposal_t = sum(r.quantity_t or 0.0 for r in records if r.charge_type == "disposal_acceptance")
    if disposal_t:
        ctx.decisions.add(
            Decision(
                category=6,
                topic="Annahme von Erdaushub und Bohrklein",
                detail=f"{_de(disposal_t)} t betreffen die Annahme von Erdaushub bzw. Bohrklein beim Lieferanten, also Entsorgung statt Lieferung.",
                impact="In der Liefermenge Schotter sind sie nicht enthalten; sie werden als eigene Kennzahl gefuehrt.",
                proposal="Bestaetigung der Trennung und Klaerung, gegen welche LV Positionen die Entsorgungsmengen abzugleichen sind.",
                evidence=f"{task.source_file}, Positionsart 'Annahme ...'",
            )
        )

    if not any(r.price_per_unit or r.amount_eur for r in records):
        ctx.decisions.add(
            Decision(
                category=6,
                topic="Keine Preise im ERP Export enthalten",
                detail="Der Wareneingangsexport enthaelt Mengen, aber keine Preise oder Betraege.",
                impact="material_cost_eur, Erloes und Marge bleiben leer. Alle Preis-Kennzahlen im Modell sind unbelegt.",
                proposal="Bestellpreise bzw. Rechnungsdaten aus IFS nachliefern (Bestellnummer P100042563, Positionsebene).",
                evidence=task.source_file,
            )
        )

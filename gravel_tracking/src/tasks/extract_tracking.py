"""T2c Bestehende Tracking Arbeitsmappe einlesen.

Zwei Aufgaben, streng getrennt:

**Ergaenzen.** Wareneingaenge, deren Bestellung kein eigener ERP Export abdeckt,
werden als Saetze aufgenommen. Ohne sie fehlt der gesamte Zeitraum vor 2026.

**Veredeln.** Fuer Wareneingaenge, die bereits aus dem ERP Export stammen, wird
die Ortsangabe uebernommen, wenn sie besser belegt ist als die eigene. Die
Mappe hat die Orte aus den Originalbelegen erarbeitet und loest damit genau die
Spannen auf, die das Notizfeld offen laesst. Die Menge der bestehenden Saetze
wird dabei nie angefasst.
"""
from __future__ import annotations

from ..conversion import convert
from ..decisions import Decision
from ..harness import Context, TaskResult
from ..materials import CHARGE_SUPPLY, MaterialInfo, factor_key
from ..state import Task
from ..tracking_reader import TrackingRow, read_tracking
from ..validators import DeliveryRecord, make_record_id, plausibility_problems


def _erp_key(record: DeliveryRecord) -> str:
    """Schluessel der Mappe aus einem ERP Satz nachbauen: PO|Position|Zeile|Empfang."""
    ref = record.source_row_ref
    if not ref.startswith("po="):
        return ""
    try:
        head, tail = ref.split(";receipt=")
        order, line = head[3:].split("/")
    except ValueError:
        return ""
    return f"{order}|{line}|1|{tail}"


def run(task: Task, ctx: Context) -> TaskResult:
    path = ctx.cfg.path("tracking_workbook")
    if path is None or not path.is_file():
        return TaskResult(ok=True, message="keine Tracking Mappe hinterlegt", data={"rows": 0})

    sheet = (ctx.cfg.get("tracking_workbook") or {}).get("sheet", "Lieferungen")
    rows = read_tracking(path, sheet)
    by_key = {row.key: row for row in rows}

    existing = ctx.store.records()
    known_orders = {r.order_no for r in existing if r.order_no}
    stats = {"rows": len(rows), "enriched": 0, "added": 0, "conflicts": 0, "added_t": 0.0}

    # 1. Veredeln
    updated: list[DeliveryRecord] = []
    for record in existing:
        row = by_key.get(_erp_key(record))
        if row is None or not row.location_label:
            continue
        better = row.location_confidence > record.location_confidence
        both_exact = record.location_type in ("point", "crossing") and row.location_type in ("point", "crossing")
        if both_exact and row.location_label != record.location_label:
            # Widerspruch zweier belegter Orte: nichts ueberschreiben.
            stats["conflicts"] += 1
            continue
        if not better:
            continue
        record.location_label = row.location_label
        record.location_type = row.location_type
        record.location_from = row.location_number
        record.location_to = row.location_number
        record.location_span_count = 1 if row.location_number is not None else 0
        record.location_source = "tracking_workbook"
        record.location_confidence = row.location_confidence
        updated.append(record)
    stats["enriched"] = len(updated)

    # 2. Ergaenzen
    missing_orders = sorted({row.order_no for row in rows if row.order_no and row.order_no not in known_orders})
    new_records: list[DeliveryRecord] = []
    for row in rows:
        if row.order_no not in missing_orders:
            continue
        new_records.append(_to_record(row, ctx, task.source_file or str(path.name)))
    stats["added"] = len(new_records)
    stats["added_t"] = round(sum(r.quantity_t or 0.0 for r in new_records), 2)

    if updated or new_records:
        ctx.store.upsert(updated + new_records)
        ctx.store.save()

    if missing_orders:
        ctx.decisions.add(Decision(
            category=6,
            topic="Mengen aus der Tracking Mappe statt aus einem ERP Export",
            detail=(
                f"Fuer die Bestellung(en) {', '.join(missing_orders)} liegt kein eigener Wareneingangsexport vor. "
                f"{stats['added']} Zeilen mit {stats['added_t']:,.0f} t stammen deshalb aus der Tracking Mappe."
            ).replace(",", "."),
            impact=(
                "Diese Zeilen tragen weniger Detail als ein ERP Export: keine Positionsart, keine Zuschlagszeilen, "
                "keine Originalbezeichnung des Materials. Zuschlaege und Annahmemengen fehlen fuer diesen Zeitraum."
            ),
            proposal="Wareneingangsexport je Bestellung ziehen, dann ersetzt er die Zeilen der Mappe automatisch.",
            evidence=f"{path.name}, Blatt {sheet}",
        ))

    if stats["conflicts"]:
        ctx.decisions.add(Decision(
            category=2,
            topic="Ortsangabe im Wareneingang und in der Tracking Mappe widersprechen sich",
            detail=f"Bei {stats['conflicts']} Wareneingaengen nennen beide Quellen einen belegten, aber verschiedenen Ort.",
            impact="Es wurde nichts ueberschrieben, es gilt weiter die Angabe aus dem Wareneingang.",
            proposal="Stichprobe gegen den Originalbeleg, danach eine der beiden Quellen als fuehrend festlegen.",
            evidence="work/02_records.csv, Spalten location_source und unload_location_text",
        ))

    ctx.log(
        f"TRACKING zeilen={stats['rows']} veredelt={stats['enriched']} ergaenzt={stats['added']} "
        f"({stats['added_t']:.0f} t) konflikte={stats['conflicts']}"
    )
    return TaskResult(ok=True, message=f"{stats['enriched']} Orte veredelt, {stats['added']} Saetze ergaenzt", data=stats)


def _to_record(row: TrackingRow, ctx: Context, source_file: str) -> DeliveryRecord:
    info = MaterialInfo(
        material_text=f"{row.material_group} {row.grain_size}".strip(),
        material_core=row.material_group,
        charge_type=CHARGE_SUPPLY,
        material_class=row.material_class,
        grain_size=row.grain_size,
        rock_type="",
        transport_class="",
    )
    conv = convert(row.quantity_t, factor_key(info), ctx.factors)
    area = row.section_key or (row.location_label if row.location_type == "crossing" else "GENERAL")
    record = DeliveryRecord(
        record_id=make_record_id("tracking", row.key),
        source_system="tracking_workbook",
        source_file=source_file,
        source_row_ref=row.key,
        doc_type="erp_receipt",
        supplier_name=ctx.cfg["suppliers"][0]["name"] if ctx.cfg.get("suppliers") else "",
        delivery_note_no=row.delivery_note_no or row.key,
        delivery_note_source="document_note" if row.delivery_note_no else "tracking_key",
        invoice_no=row.invoice_no,
        order_no=row.order_no,
        delivery_date=row.delivery_date,
        material_text=info.material_text,
        material_class=info.material_class,
        grain_size=info.grain_size,
        charge_type=CHARGE_SUPPLY,
        quantity=row.quantity_t,
        unit="ton",
        quantity_t=row.quantity_t,
        delivered_m3_loose=conv.m3_loose,
        delivered_m3_installed=conv.m3_installed,
        delivered_m3_installed_low=conv.m3_installed_low,
        delivered_m3_installed_high=conv.m3_installed_high,
        conversion_source=conv.source,
        conversion_confidence=conv.confidence,
        unload_location_text=row.location_label,
        area_from_folder=area,
        area_final=area,
        area_class="",
        location_type=row.location_type,
        location_label=row.location_label,
        location_from=row.location_number,
        location_to=row.location_number,
        location_span_count=1 if row.location_number is not None else 0,
        location_source="tracking_workbook",
        location_confidence=row.location_confidence,
        extraction_method="tracking_workbook",
        extraction_confidence=max(row.location_confidence, 0.9),
    )
    from ..areas import classify_area

    record.area_class = classify_area(record.area_final, ctx.cfg["areas"]["classes"])
    problems = plausibility_problems(record, ctx.cfg.raw)
    if problems:
        record.needs_review = True
        record.review_reason = ";".join(sorted(set(problems)))
    return record

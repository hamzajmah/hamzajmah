"""T2 Strukturierte Extraktion aus PDF Rohtext.

Zuerst deterministisches Parsing ueber die Lieferantenvorlage. LLM gestuetzte
Extraktion ist ausschliesslich Eskalationsstufe fuer abweichende Dokumente und
nur aktiv, wenn sie in der Konfiguration eingeschaltet ist.
"""
from __future__ import annotations

from ..areas import resolve
from ..config import load_supplier_templates
from ..conversion import convert
from ..harness import Context, TaskResult
from ..materials import classify, factor_key
from ..state import Task
from ..store import RecordStore
from ..validators import DeliveryRecord, check_record, make_record_id
from .text import text_path


def run(task: Task, ctx: Context) -> TaskResult:
    level = task.escalation_level
    if level >= 3:
        if not ctx.cfg["extraction"].get("llm_enabled", False):
            return TaskResult(
                ok=False,
                message="LLM gestuetzte Extraktion ist nicht konfiguriert (extraction.llm_enabled=false)",
                error_class="llm_not_configured",
            )
        return TaskResult(ok=False, message="LLM Stufe nicht implementiert", error_class="llm_not_implemented")

    path = text_path(ctx, task.source_file, task.content_hash)
    if not path.exists():
        return TaskResult(ok=False, message="kein Rohtext vorhanden", error_class="missing_text", escalate=False)

    text = path.read_text(encoding="utf-8")
    templates = load_supplier_templates(ctx.cfg)
    from ..parsing import detect_supplier, parse_document

    template = detect_supplier(text, templates)
    if template is None:
        return TaskResult(ok=False, message="kein Lieferant erkannt", error_class="unknown_supplier")

    area_patterns = ctx.cfg["areas"]["classes"]
    factors = ctx.factors
    records: list[DeliveryRecord] = []
    problems_all: list[str] = []

    for note in parse_document(text, template):
        info = classify(note.material_text)
        conv = convert(note.quantity_t, factor_key(info), factors)
        area = resolve(str(task.payload.get("area_from_folder", "")), "", area_patterns)
        rec = DeliveryRecord(
            record_id=make_record_id("pdf", task.source_file, note.page, note.delivery_note_no, note.quantity_t),
            source_system="pdf",
            source_file=task.source_file,
            source_page=note.page,
            doc_type=str(task.payload.get("doc_type_guess", "unknown")),
            supplier_name=str(template.get("supplier_name", "")),
            delivery_note_no=note.delivery_note_no,
            delivery_note_source="document" if note.delivery_note_no else "",
            order_no=note.order_no,
            delivery_date=note.delivery_date,
            material_text=note.material_text,
            material_class=info.material_class,
            grain_size=info.grain_size,
            rock_type=info.rock_type,
            transport_class=info.transport_class,
            charge_type=info.charge_type,
            quantity=note.quantity_t,
            unit="ton" if note.quantity_t is not None else "",
            quantity_t=note.quantity_t,
            quantity_m3_doc=note.quantity_m3_doc,
            delivered_m3_loose=conv.m3_loose,
            delivered_m3_installed=conv.m3_installed,
            delivered_m3_installed_low=conv.m3_installed_low,
            delivered_m3_installed_high=conv.m3_installed_high,
            conversion_source=conv.source,
            conversion_confidence=conv.confidence,
            area_from_folder=area.area_from_folder,
            area_from_document=area.area_from_document,
            area_final=area.area_final,
            area_class=area.area_class,
            area_conflict=area.area_conflict,
            vehicle_id=note.vehicle_id,
            extraction_method="template" if level == 0 else f"template_after_ocr_l{level}",
            extraction_confidence=note.confidence,
        )
        problems = check_record(rec, ctx.cfg.raw)
        if problems:
            rec.needs_review = True
            rec.review_reason = ";".join(problems)
            problems_all.extend(problems)
        records.append(rec)

    if not records:
        return TaskResult(ok=False, message="keine Position erkannt", error_class="no_records")

    # Auch unvollstaendige Saetze werden persistiert, damit die Pruefliste sie
    # sieht. Der Task scheitert trotzdem und eskaliert.
    _persist(ctx.store, task.source_file, records)
    if problems_all:
        return TaskResult(ok=False, message="Abnahme nicht bestanden: " + ";".join(sorted(set(problems_all))[:5]), error_class="acceptance_failed")

    return TaskResult(ok=True, message=f"{len(records)} Saetze", data={"records": len(records)})


def _persist(store: RecordStore, source_file: str, records: list[DeliveryRecord]) -> None:
    store.drop_source(source_file)
    store.upsert(records)

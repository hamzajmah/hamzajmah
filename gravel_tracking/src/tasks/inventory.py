"""T0 Inventarisierung.

Rekursiver Scan aller Eingangsquellen. Jede Datei wird genau einmal erfasst und
einem Bereich oder _unsorted zugeordnet. Aus dem Inventar entstehen die
nachgelagerten Tasks.
"""
from __future__ import annotations

import csv
from pathlib import Path

from ..harness import Context, TaskResult
from ..state import DONE, PARKED, PENDING, Task, file_hash

INVENTORY_COLUMNS = [
    "source_id",
    "kind",
    "path",
    "folder",
    "area_from_folder",
    "pages",
    "has_text_layer",
    "doc_type_guess",
    "size_bytes",
    "content_hash",
]


def _pdf_meta(path: Path) -> tuple[int, bool]:
    try:
        import pdfplumber
    except Exception:  # pragma: no cover - Umgebung ohne funktionsfaehiges pdfplumber
        return 0, False
    try:
        with pdfplumber.open(str(path)) as pdf:
            pages = len(pdf.pages)
            text = ""
            for page in pdf.pages[:3]:
                text += page.extract_text() or ""
            return pages, len(text.strip()) > 0
    except Exception:
        return 0, False


def _doc_type_guess(name: str) -> str:
    low = name.lower()
    if "rechnung" in low or "invoice" in low:
        return "invoice"
    if "lieferschein" in low or low.startswith("ls"):
        return "delivery_note"
    return "unknown"


def run(task: Task, ctx: Context) -> TaskResult:
    rows: list[dict[str, object]] = []

    pdf_root = ctx.cfg.path("pdf_root")
    if pdf_root and pdf_root.exists():
        for path in sorted(pdf_root.rglob("*.pdf")):
            rel_folder = path.parent.relative_to(pdf_root).as_posix() or "_root"
            area = rel_folder.split("/")[0]
            pages, has_text = _pdf_meta(path)
            rows.append(
                {
                    "source_id": path.relative_to(ctx.cfg.root).as_posix(),
                    "kind": "pdf",
                    "path": path.relative_to(ctx.cfg.root).as_posix(),
                    "folder": rel_folder,
                    "area_from_folder": "" if area.startswith("_") else area,
                    "pages": pages,
                    "has_text_layer": "true" if has_text else "false",
                    "doc_type_guess": _doc_type_guess(path.name),
                    "size_bytes": path.stat().st_size,
                    "content_hash": file_hash(path),
                }
            )

    erp_root = ctx.cfg.path("erp_exports")
    if erp_root and erp_root.exists():
        for path in sorted(list(erp_root.glob("*.xlsx")) + list(erp_root.glob("*.xls"))):
            rows.append(
                {
                    "source_id": path.relative_to(ctx.cfg.root).as_posix(),
                    "kind": "erp",
                    "path": path.relative_to(ctx.cfg.root).as_posix(),
                    "folder": erp_root.name,
                    "area_from_folder": "",
                    "pages": 0,
                    "has_text_layer": "true",
                    "doc_type_guess": "erp_receipt_export",
                    "size_bytes": path.stat().st_size,
                    "content_hash": file_hash(path),
                }
            )

    inv_path = ctx.work_dir / "00_inventory.csv"
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    with inv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=INVENTORY_COLUMNS, delimiter=";", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    # Abnahme: jede Datei genau einmal, jede einem Bereich oder _unsorted.
    ids = [r["source_id"] for r in rows]
    if len(ids) != len(set(ids)):
        return TaskResult(ok=False, message="Quelle mehrfach inventarisiert", error_class="inventory_duplicate", escalate=False)

    created = _spawn_downstream(rows, ctx)
    ctx.log(f"INVENTORY quellen={len(rows)} pdf={sum(1 for r in rows if r['kind'] == 'pdf')} erp={sum(1 for r in rows if r['kind'] == 'erp')} neue_tasks={created}")
    return TaskResult(ok=True, message=f"{len(rows)} Quellen inventarisiert", data={"sources": len(rows), "new_tasks": created})


def _spawn_downstream(rows: list[dict[str, object]], ctx: Context) -> int:
    state = ctx.state
    created = 0
    doc_task_ids: list[str] = []

    for row in rows:
        source_id = str(row["source_id"])
        content_hash = str(row["content_hash"])
        if row["kind"] == "pdf":
            text_id = f"T1:{source_id}"
            extract_id = f"T2:{source_id}"
            created += int(state.add(Task(task_id=text_id, type="text_extract", priority=20, source_file=source_id, content_hash=content_hash, payload=dict(row))))
            created += int(state.add(Task(task_id=extract_id, type="extract_pdf", priority=30, source_file=source_id, content_hash=content_hash, depends_on=[text_id], payload=dict(row))))
            doc_task_ids += [text_id, extract_id]
        else:
            extract_id = f"T2:{source_id}"
            created += int(state.add(Task(task_id=extract_id, type="extract_erp", priority=30, source_file=source_id, content_hash=content_hash, payload=dict(row))))
            doc_task_ids.append(extract_id)

    master_id = "T0b:master_data"
    created += int(state.add(Task(task_id=master_id, type="master_data", priority=15)))

    clean_id, location_id = "T3:clean", "T3b:locations"
    match_id, build_id, report_id = "T4:lv_match", "T5:build_model", "T6:report"
    reconcile_id, invoice_id = "T4b:reconcile", "T4c:invoices"
    created += int(state.add(Task(task_id=clean_id, type="clean", priority=50, depends_on=sorted(doc_task_ids))))
    created += int(state.add(Task(task_id=location_id, type="locations", priority=55, depends_on=[clean_id])))
    created += int(state.add(Task(task_id=match_id, type="lv_match", priority=60, depends_on=[clean_id, location_id])))
    created += int(state.add(Task(task_id=reconcile_id, type="reconcile", priority=58, depends_on=[clean_id])))
    created += int(state.add(Task(task_id=invoice_id, type="invoices", priority=59, depends_on=[clean_id])))
    created += int(state.add(Task(task_id=build_id, type="build_model", priority=70, depends_on=[match_id, reconcile_id, invoice_id])))
    created += int(state.add(Task(task_id=report_id, type="report", priority=80, depends_on=[build_id])))

    state.tasks[clean_id].depends_on = sorted(set(state.tasks[clean_id].depends_on) | set(doc_task_ids))

    # Aggregattasks muessen nur dann erneut laufen, wenn sich am Bestand etwas
    # geaendert hat. Genau das macht den zweiten Lauf idempotent.
    if created:
        for tid in (master_id, clean_id, location_id, reconcile_id, invoice_id, match_id, build_id, report_id):
            agg = state.tasks[tid]
            if agg.status in (DONE, PARKED):
                agg.status = PENDING
                agg.attempts_at_level = 0
                agg.escalation_level = 0
    return created

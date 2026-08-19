"""T3 Bereinigung, Dedup, Plausibilitaet."""
from __future__ import annotations

import csv
import json
from collections import defaultdict

from ..decisions import Decision
from ..harness import Context, TaskResult
from ..materials import CHARGE_SUPPLY
from ..state import Task
from ..validators import DeliveryRecord

DUPLICATE_COLUMNS = ["record_id", "dedup_key", "supplier_name", "delivery_note_no", "delivery_date", "material_text", "quantity_t", "extraction_confidence", "kept_record_id", "reason"]
REVIEW_COLUMNS = ["record_id", "source_system", "source_file", "source_row_ref", "delivery_date", "area_final", "material_text", "charge_type", "quantity_t", "extraction_confidence", "review_reason"]


def _dedup_key(rec: DeliveryRecord) -> str:
    """Dedup Schluessel nach Abschnitt 7.

    Um Positionsarten derselben Fuhre nicht faelschlich zu verschmelzen, geht
    die Positionsart in den Schluessel ein: eine Fuhre erzeugt im ERP je eine
    Zeile fuer Material und je eine fuer Diesel- und Samstagszuschlag.
    """
    return "|".join([
        rec.supplier_name,
        rec.delivery_note_no,
        rec.delivery_date.isoformat() if rec.delivery_date else "",
        rec.charge_type,
        rec.material_class,
        rec.grain_size,
    ])


def run(task: Task, ctx: Context) -> TaskResult:
    records = ctx.store.records()
    if not records:
        return TaskResult(ok=False, message="keine Saetze vorhanden", error_class="no_records", escalate=False)

    groups: dict[str, list[DeliveryRecord]] = defaultdict(list)
    for rec in records:
        rec.is_duplicate = False
        rec.dedup_key = _dedup_key(rec)
        # Nur Saetze mit einer echten Lieferscheinnummer aus dem Dokument
        # nehmen an der Dedup teil. Ersatzschluessel aus dem ERP sind bereits
        # zeilenscharf eindeutig.
        if rec.delivery_note_source == "document_note" and rec.delivery_note_no:
            groups[rec.dedup_key].append(rec)

    duplicates: list[dict[str, object]] = []
    dup_by_type: dict[str, list[float]] = defaultdict(list)
    ambiguous = 0
    for key, group in sorted(groups.items()):
        if len(group) < 2:
            continue
        quantities = {round(r.quantity_t or -1.0, 3) for r in group}
        if len(quantities) == 1:
            # Sieger: hoechste Konfidenz, danach der bereichsscharf gebuchte
            # Satz, danach die kleinere record_id (deterministisch).
            winner = sorted(
                group,
                key=lambda r: (-r.extraction_confidence, r.area_class == "general", r.record_id),
            )[0]
            for loser in group:
                if loser.record_id == winner.record_id:
                    continue
                loser.is_duplicate = True
                dup_by_type[loser.charge_type].append(loser.quantity_t or 0.0)
                duplicates.append({
                    "record_id": loser.record_id, "dedup_key": key, "supplier_name": loser.supplier_name,
                    "delivery_note_no": loser.delivery_note_no,
                    "delivery_date": loser.delivery_date.isoformat() if loser.delivery_date else "",
                    "material_text": loser.material_text, "quantity_t": loser.quantity_t,
                    "extraction_confidence": loser.extraction_confidence, "kept_record_id": winner.record_id,
                    "reason": "identischer Dedup Schluessel und identische Menge",
                })
        else:
            ambiguous += 1
            for rec in group:
                reasons = set(filter(None, rec.review_reason.split(";")))
                reasons.add("lieferscheinnummer_mehrfach_mit_abweichender_menge")
                rec.review_reason = ";".join(sorted(reasons))
                rec.needs_review = True

    if duplicates:
        summary = ", ".join(
            f"{charge}: {len(values)} Zeilen / {sum(values):.1f} t" for charge, values in sorted(dup_by_type.items())
        )
        ctx.decisions.add(Decision(
            category=5,
            topic="Doppelt gebuchte Lieferscheine",
            detail=(
                f"{len(duplicates)} Zeilen tragen dieselbe Lieferscheinnummer, dasselbe Datum, dieselbe Positionsart und "
                f"dieselbe Menge wie eine andere Zeile ({summary}). Sie stammen ueberwiegend aus zwei verschiedenen "
                "Wareneingangsbelegen mit unterschiedlichem Erfasser."
            ),
            impact=(
                "Die Pipeline zaehlt jede dieser Fuhren genau einmal. Fuer die Kostenseite ist die Doppelbuchung "
                "aber relevant, weil sie im ERP zweimal steht."
            ),
            proposal=(
                "Stichprobe gegen die Originallieferscheine, danach Korrektur der Doppelbuchungen im IFS. "
                "Liste in work/03_duplicates.csv."
            ),
            evidence="work/03_duplicates.csv",
        ))

    if ambiguous:
        ctx.decisions.add(Decision(
            category=5,
            topic="Mehrfach verwendete Lieferscheinnummern mit abweichender Menge",
            detail=f"{ambiguous} Lieferscheinnummern treten mehrfach mit unterschiedlicher Menge auf.",
            impact="Es ist ohne Originalbeleg nicht entscheidbar, ob Teillieferungen oder Doppelbuchungen vorliegen. Es wurde nichts verworfen, alle Saetze bleiben in der Menge und stehen in der Pruefliste.",
            proposal="Stichprobe gegen die Originallieferscheine, danach Regel festlegen (Teillieferung zulassen oder Doppelbuchung verwerfen).",
            evidence="work/04_review_queue.csv, Grund lieferscheinnummer_mehrfach_mit_abweichender_menge",
        ))

    # Dichteannahmen sichtbar machen: sie bestimmen jede m3 Aussage.
    assumed = sorted({r.conversion_source for r in records if r.conversion_confidence == "assumption" and r.conversion_source})
    if assumed:
        ctx.decisions.add(Decision(
            category=1,
            topic="Dichtewerte sind nicht durch ein Projektdokument belegt",
            detail="Alle Umrechnungen von Tonnen in Kubikmeter beruhen auf Literaturbandbreiten aus config/conversion_factors.yaml: " + "; ".join(assumed),
            impact="Jede m3 Angabe und damit jedes Delta gegen das LV ist eine Annahme, keine Messung. Die Sensitivitaet betraegt plus minus 10 Prozent.",
            proposal="Lieferantendatenblatt oder einen Beleg mit Tonnen und Kubikmetern beibringen, danach projektspezifische Faktoren eintragen (Feld source, confidence: measured).",
            evidence="config/conversion_factors.yaml",
        ))

    unknown_areas = sorted({r.area_final for r in records if r.area_class == "unknown"})
    if unknown_areas:
        ctx.decisions.add(Decision(
            category=3,
            topic="Bereichsschluessel ausserhalb des bekannten Musters",
            detail="Diese Buchungsschluessel passen weder zum Muster der Trassenabschnitte noch zu dem der Querungen: "
                   + ", ".join(unknown_areas),
            impact="Die betroffenen Mengen erscheinen in der Auswertung als eigener Bereich und lassen sich keiner Bereichsgruppe zuordnen.",
            proposal="Fachliche Zuordnung dieser Schluessel zu Abschnitt, Querung oder einer weiteren Bereichsgruppe, danach Muster in config/config.yaml unter areas.classes ergaenzen.",
            evidence="work/02_records.csv, Spalte area_final",
        ))

    supply = [r for r in records if r.charge_type == CHARGE_SUPPLY and not r.is_duplicate]
    total_t = sum(r.quantity_t or 0.0 for r in supply)
    review_t = sum(r.quantity_t or 0.0 for r in supply if r.needs_review)
    review_share = 100.0 * review_t / total_t if total_t else 0.0
    threshold = float(ctx.cfg["plausibility"]["review_share_threshold_pct"])
    provisional = review_share >= threshold

    _write_csv(ctx.work_dir / "03_duplicates.csv", DUPLICATE_COLUMNS, duplicates)
    _write_csv(
        ctx.work_dir / "04_review_queue.csv",
        REVIEW_COLUMNS,
        [
            {
                "record_id": r.record_id, "source_system": r.source_system, "source_file": r.source_file,
                "source_row_ref": r.source_row_ref,
                "delivery_date": r.delivery_date.isoformat() if r.delivery_date else "",
                "area_final": r.area_final, "material_text": r.material_text, "charge_type": r.charge_type,
                "quantity_t": r.quantity_t, "extraction_confidence": r.extraction_confidence,
                "review_reason": r.review_reason,
            }
            for r in records if r.needs_review
        ],
    )

    quality = {
        "records_total": len(records),
        "records_supply": len(supply),
        "duplicates_removed": len(duplicates),
        "review_records": sum(1 for r in records if r.needs_review),
        "supply_t_total": round(total_t, 2),
        "supply_t_in_review": round(review_t, 2),
        "review_share_pct": round(review_share, 2),
        "review_share_threshold_pct": threshold,
        "result_provisional": provisional,
    }
    (ctx.work_dir / "quality.json").write_text(json.dumps(quality, indent=2, ensure_ascii=False), encoding="utf-8")
    ctx.store.save()

    ctx.log(
        f"CLEAN saetze={len(records)} duplikate={len(duplicates)} pruefliste={quality['review_records']} "
        f"anteil_menge_pruefliste={review_share:.2f}% vorlaeufig={provisional}"
    )
    # Abnahme: die Schwelle wird gemessen und ausgewiesen. Wird sie verfehlt,
    # laeuft der Harness weiter und markiert das Ergebnis als vorlaeufig.
    return TaskResult(ok=True, message=f"Pruefliste {review_share:.2f} Prozent der Menge", data=quality)


def _write_csv(path, columns, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, delimiter=";", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

"""T4 Abgleich Lieferung gegen Leistungsverzeichnis.

Der Vergleich erfolgt ausschliesslich gegen delivered_m3_installed. Fachliche
Einordnungen von Deltas sind ausdruecklich Hypothesen und stehen im Bericht,
nicht in den Zahlen.
"""
from __future__ import annotations

import csv
from collections import defaultdict

from ..decisions import Decision
from ..harness import Context, TaskResult
from ..materials import CHARGE_SUPPLY
from ..state import Task

LV_COLUMNS = ["lv_position_no", "short_text", "unit", "contract_quantity", "billed_quantity", "unit_price_eur", "area_id", "source"]
COMPARISON_COLUMNS = [
    "area_final", "area_class", "period_month", "material_class", "grain_size",
    "delivered_t", "delivered_m3_loose", "delivered_m3_installed",
    "delivered_m3_installed_low", "delivered_m3_installed_high",
    "billed_m3", "delta_m3", "delta_m3_low", "delta_m3_high", "coverage_pct",
    "material_cost_eur", "billed_revenue_eur", "margin_eur",
    "conversion_source", "conversion_confidence", "records", "records_in_review", "data_basis",
]


def _read_lv(ctx: Context) -> list[dict[str, object]]:
    path = ctx.cfg.path("lv_file")
    if path is None or not path.is_file():
        return []
    import pandas as pd

    frame = pd.read_excel(path)
    mapping = ctx.cfg.get("lv", {}).get("columns", {}) if isinstance(ctx.cfg.get("lv"), dict) else {}
    rows = []
    for row in frame.to_dict("records"):
        rows.append({col: row.get(mapping.get(col, col), "") for col in LV_COLUMNS})
    return rows


def run(task: Task, ctx: Context) -> TaskResult:
    lv_rows = _read_lv(ctx)
    _write_csv(ctx.work_dir / "05_lv_positions.csv", LV_COLUMNS, lv_rows)

    if not lv_rows:
        ctx.decisions.add(Decision(
            category=3,
            topic="Leistungsverzeichnis und Aufmassstand liegen nicht vor",
            detail="In config/config.yaml ist unter paths.lv_file keine Datei hinterlegt, es wurde keine LV Position eingelesen.",
            impact="Die Spalten billed_m3, delta_m3, coverage_pct, Erloes und Marge bleiben leer. Die Auswertung zeigt die Lieferseite, nicht den Abgleich.",
            proposal="Aufmass bzw. LV Auszug bereitstellen (Positionsnummer, Kurztext, Einheit, Vertragsmenge, abgerechnete Menge, Bereichszuordnung) und Pfad in config.yaml eintragen.",
            evidence="config/config.yaml, paths.lv_file",
        ))

    billed_by_area: dict[tuple[str, str], float] = defaultdict(float)
    revenue_by_area: dict[tuple[str, str], float] = defaultdict(float)
    for row in lv_rows:
        area = str(row.get("area_id") or "")
        qty = _num(row.get("billed_quantity")) or 0.0
        price = _num(row.get("unit_price_eur")) or 0.0
        billed_by_area[(area, "")] += qty
        revenue_by_area[(area, "")] += qty * price

    buckets: dict[tuple, dict[str, float]] = {}
    bucket_meta: dict[tuple, tuple[str, str]] = {}
    for rec in ctx.store.records():
        if rec.charge_type != CHARGE_SUPPLY or rec.is_duplicate:
            continue
        period = rec.delivery_date.strftime("%Y-%m") if rec.delivery_date else ""
        key = (rec.area_final, rec.area_class, period, rec.material_class, rec.grain_size)
        b = buckets.setdefault(key, {
            "delivered_t": 0.0, "delivered_m3_loose": 0.0, "delivered_m3_installed": 0.0,
            "delivered_m3_installed_low": 0.0, "delivered_m3_installed_high": 0.0,
            "material_cost_eur": 0.0, "records": 0.0, "records_in_review": 0.0,
        })
        bucket_meta.setdefault(key, (rec.conversion_source, rec.conversion_confidence))
        b["delivered_t"] += rec.quantity_t or 0.0
        b["delivered_m3_loose"] += rec.delivered_m3_loose or 0.0
        b["delivered_m3_installed"] += rec.delivered_m3_installed or 0.0
        b["delivered_m3_installed_low"] += rec.delivered_m3_installed_low or 0.0
        b["delivered_m3_installed_high"] += rec.delivered_m3_installed_high or 0.0
        b["material_cost_eur"] += rec.amount_eur or 0.0
        b["records"] += 1.0
        b["records_in_review"] += 1.0 if rec.needs_review else 0.0

    rows = []
    for (area, area_class, period, material_class, grain), b in sorted(buckets.items()):
        billed = billed_by_area.get((area, ""), 0.0) if lv_rows else None
        installed = round(b["delivered_m3_installed"], 2)
        delta = round(billed - installed, 2) if billed is not None else None
        coverage = round(100.0 * billed / installed, 2) if billed is not None and installed else None
        rows.append({
            "area_final": area, "area_class": area_class, "period_month": period,
            "material_class": material_class, "grain_size": grain,
            "delivered_t": round(b["delivered_t"], 2),
            "delivered_m3_loose": round(b["delivered_m3_loose"], 2),
            "delivered_m3_installed": installed,
            "delivered_m3_installed_low": round(b["delivered_m3_installed_low"], 2),
            "delivered_m3_installed_high": round(b["delivered_m3_installed_high"], 2),
            "billed_m3": billed,
            "delta_m3": delta,
            "delta_m3_low": round(billed - b["delivered_m3_installed_high"], 2) if billed is not None else None,
            "delta_m3_high": round(billed - b["delivered_m3_installed_low"], 2) if billed is not None else None,
            "coverage_pct": coverage,
            "material_cost_eur": round(b["material_cost_eur"], 2) or None,
            "billed_revenue_eur": round(revenue_by_area.get((area, ""), 0.0), 2) if lv_rows else None,
            "margin_eur": None,
            "conversion_source": bucket_meta[(area, area_class, period, material_class, grain)][0],
            "conversion_confidence": bucket_meta[(area, area_class, period, material_class, grain)][1],
            "records": int(b["records"]),
            "records_in_review": int(b["records_in_review"]),
            "data_basis": "Lieferseite belegt, LV Seite offen" if not lv_rows else "Lieferung gegen LV",
        })

    _write_csv(ctx.work_dir / "06_comparison.csv", COMPARISON_COLUMNS, rows)

    # Abnahme: jede Zeile hat Bereich, Periode und eine nachvollziehbare Umrechnungsquelle.
    bad = [r for r in rows if not r["area_final"] or not r["period_month"] or not r["conversion_source"]]
    if bad:
        return TaskResult(ok=False, message=f"{len(bad)} Vergleichszeilen ohne Bereich, Periode oder Umrechnungsquelle", error_class="comparison_incomplete")

    ctx.log(f"LV_MATCH vergleichszeilen={len(rows)} lv_positionen={len(lv_rows)}")
    return TaskResult(ok=True, message=f"{len(rows)} Vergleichszeilen", data={"rows": len(rows), "lv_positions": len(lv_rows)})


def _num(value: object) -> float | None:
    """Zahl aus dem LV Auszug, tolerant gegen deutsches Dezimalkomma."""
    if value is None or value == "":
        return None
    text = str(value)
    try:
        return float(text.replace(".", "").replace(",", ".")) if "," in text else float(text)
    except ValueError:
        return None


def _write_csv(path, columns, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, delimiter=";", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})

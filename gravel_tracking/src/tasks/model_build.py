"""T5 Power BI Modell als Excel Arbeitsmappe (Sternschema)."""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

from ..excel_out import verify_workbook, write_workbook
from ..harness import Context, TaskResult
from ..state import Task

FACT_DELIVERY = [
    "record_id", "delivery_date", "area_key", "material_key", "supplier_key", "lv_position_no",
    "charge_type", "doc_type", "source_system", "source_file", "source_row_ref",
    "delivery_note_no", "delivery_note_source", "activity_id", "activity_text", "unload_location_text",
    "quantity", "unit", "quantity_t", "quantity_m3_doc",
    "delivered_m3_loose", "delivered_m3_installed", "delivered_m3_installed_low", "delivered_m3_installed_high",
    "conversion_confidence", "price_per_unit", "amount_eur",
    "extraction_method", "extraction_confidence", "is_duplicate", "needs_review", "review_reason",
]
FACT_LV_BILLING = ["lv_position_no", "area_key", "period_month", "unit", "contract_quantity", "billed_quantity", "unit_price_eur", "billed_revenue_eur", "source"]
DIM_AREA = ["area_key", "area_name", "area_class", "area_group", "is_assigned"]
DIM_DATE = ["date", "year", "month", "year_month", "month_name_de", "quarter", "iso_week", "is_weekend"]
DIM_MATERIAL = ["material_key", "material_class", "grain_size", "rock_type", "is_gravel_supply", "bulk_density_t_per_m3", "installed_density_t_per_m3", "compaction_factor", "conversion_confidence", "conversion_source"]
DIM_SUPPLIER = ["supplier_key", "supplier_name", "records"]
DIM_LV_POSITION = ["lv_position_no", "short_text", "unit", "contract_quantity", "area_key", "source"]

MONTHS_DE = ["Januar", "Februar", "Maerz", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]


def _material_key(material_class: str, grain_size: str) -> str:
    return f"{material_class or 'unbekannt'}|{grain_size or '-'}"


def _supplier_key(name: str) -> str:
    return (name or "unbekannt").strip()[:80]


def run(task: Task, ctx: Context) -> TaskResult:
    records = [r for r in ctx.store.records() if not r.is_duplicate]
    factors = (ctx.factors.get("factors") or {})

    fact_rows = []
    areas: dict[str, list[str]] = {}
    materials: dict[str, list] = {}
    suppliers: dict[str, int] = {}

    for rec in records:
        area_key = rec.area_final or "GENERAL"
        material_key = _material_key(rec.material_class, rec.grain_size)
        supplier_key = _supplier_key(rec.supplier_name)
        areas.setdefault(area_key, [rec.area_class or "unknown"])
        suppliers[supplier_key] = suppliers.get(supplier_key, 0) + 1
        if material_key not in materials:
            factor_key = f"{rec.material_class}_{rec.grain_size.replace('/', '_')}" if rec.material_class and rec.grain_size else ""
            entry = factors.get(factor_key, {})
            materials[material_key] = [
                material_key, rec.material_class or "unbekannt", rec.grain_size or "-", rec.rock_type or "-",
                1 if rec.charge_type == "material_supply" else 0,
                entry.get("bulk_density_t_per_m3"), entry.get("installed_density_t_per_m3"),
                entry.get("compaction_factor"), rec.conversion_confidence or "none", rec.conversion_source or "",
            ]
        fact_rows.append([
            rec.record_id, rec.delivery_date, area_key, material_key, supplier_key, "",
            rec.charge_type, rec.doc_type, rec.source_system, rec.source_file, rec.source_row_ref,
            rec.delivery_note_no, rec.delivery_note_source, rec.activity_id, rec.activity_text, rec.unload_location_text,
            rec.quantity, rec.unit, rec.quantity_t, rec.quantity_m3_doc,
            rec.delivered_m3_loose, rec.delivered_m3_installed, rec.delivered_m3_installed_low, rec.delivered_m3_installed_high,
            rec.conversion_confidence, rec.price_per_unit, rec.amount_eur,
            rec.extraction_method, rec.extraction_confidence, rec.is_duplicate, rec.needs_review, rec.review_reason,
        ])

    area_rows = []
    for key in sorted(areas):
        area_class = areas[key][0]
        group = {"section": "Trassenabschnitte", "crossing": "Querungen", "general": "Nicht bereichsscharf"}.get(area_class, "Sonstige")
        area_rows.append([key, key, area_class, group, 0 if area_class == "general" else 1])

    start, end = ctx.cfg.period
    date_rows = []
    day = start
    while day <= end:
        date_rows.append([
            day, day.year, day.month, f"{day.year}-{day.month:02d}", MONTHS_DE[day.month - 1],
            f"Q{(day.month - 1) // 3 + 1}", int(day.strftime("%V")), 1 if day.weekday() >= 5 else 0,
        ])
        day += timedelta(days=1)

    lv_rows, lv_positions = _read_lv(ctx.work_dir / "05_lv_positions.csv")

    tables = {
        "fact_delivery": (FACT_DELIVERY, fact_rows),
        "fact_lv_billing": (FACT_LV_BILLING, lv_rows),
        "dim_area": (DIM_AREA, area_rows),
        "dim_date": (DIM_DATE, date_rows),
        "dim_material": (DIM_MATERIAL, [materials[k] for k in sorted(materials)]),
        "dim_supplier": (DIM_SUPPLIER, [[k, k, suppliers[k]] for k in sorted(suppliers)]),
        "dim_lv_position": (DIM_LV_POSITION, lv_positions),
    }

    workbook = ctx.cfg.root / ctx.cfg["powerbi"]["workbook"]
    write_workbook(workbook, tables)

    problems = verify_workbook(workbook, {name: cols for name, (cols, _) in tables.items()})
    problems += _referential_problems(fact_rows, area_rows, date_rows, materials, suppliers, start, end)
    if problems:
        return TaskResult(ok=False, message="Modellpruefung: " + ";".join(sorted(set(problems))[:6]), error_class="model_invalid")

    _write_dax(ctx.output_dir / "powerbi" / "dax_measures.txt")
    _write_report_structure(ctx.output_dir / "powerbi" / "report_structure.md", ctx)

    ctx.log(f"MODEL fakten={len(fact_rows)} bereiche={len(area_rows)} material={len(materials)} datum={len(date_rows)}")
    return TaskResult(ok=True, message=f"{len(fact_rows)} Faktenzeilen", data={"facts": len(fact_rows), "areas": len(area_rows)})


def _read_lv(path: Path) -> tuple[list[list], list[list]]:
    if not path.exists():
        return [], []
    facts, dims = [], []
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            facts.append([
                row.get("lv_position_no", ""), row.get("area_id", ""), "", row.get("unit", ""),
                _num(row.get("contract_quantity")), _num(row.get("billed_quantity")), _num(row.get("unit_price_eur")),
                _mul(row.get("billed_quantity"), row.get("unit_price_eur")), row.get("source", ""),
            ])
            dims.append([
                row.get("lv_position_no", ""), row.get("short_text", ""), row.get("unit", ""),
                _num(row.get("contract_quantity")), row.get("area_id", ""), row.get("source", ""),
            ])
    return facts, dims


def _num(value) -> float | None:
    try:
        return float(str(value).replace(",", ".")) if value not in (None, "") else None
    except ValueError:
        return None


def _mul(a, b) -> float | None:
    x, y = _num(a), _num(b)
    return round(x * y, 2) if x is not None and y is not None else None


def _referential_problems(fact_rows, area_rows, date_rows, materials, suppliers, start: date, end: date) -> list[str]:
    problems = []
    area_keys = {r[0] for r in area_rows}
    date_keys = {r[0] for r in date_rows}
    for row in fact_rows:
        if row[2] not in area_keys:
            problems.append(f"fk_area_fehlt:{row[2]}")
        if row[3] not in materials:
            problems.append(f"fk_material_fehlt:{row[3]}")
        if row[4] not in suppliers:
            problems.append(f"fk_supplier_fehlt:{row[4]}")
        if row[1] is not None and row[1] not in date_keys:
            problems.append(f"fk_datum_ausserhalb_dim_date:{row[1]}")
    expected_days = (end - start).days + 1
    if len(date_rows) != expected_days:
        problems.append("dim_date_unvollstaendig")
    return problems


def _write_dax(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """// DAX Measures fuer gravel_model.xlsx
// Konvention: Liefermengen zaehlen ausschliesslich charge_type = "material_supply".
// Zuschlags- und Frachtzeilen tragen Tonnen nur als Abrechnungsbasis.

Liefermenge t =
CALCULATE ( SUM ( fact_delivery[quantity_t] ), fact_delivery[charge_type] = "material_supply" )

Liefermenge m3 eingebaut =
CALCULATE ( SUM ( fact_delivery[delivered_m3_installed] ), fact_delivery[charge_type] = "material_supply" )

Liefermenge m3 lose =
CALCULATE ( SUM ( fact_delivery[delivered_m3_loose] ), fact_delivery[charge_type] = "material_supply" )

Annahmemenge t =
CALCULATE ( SUM ( fact_delivery[quantity_t] ), fact_delivery[charge_type] = "disposal_acceptance" )

Abgerechnete Menge m3 = SUM ( fact_lv_billing[billed_quantity] )

Delta m3 = [Abgerechnete Menge m3] - [Liefermenge m3 eingebaut]

Delta m3 Sensitivitaet unten =
[Abgerechnete Menge m3]
    - CALCULATE ( SUM ( fact_delivery[delivered_m3_installed_high] ), fact_delivery[charge_type] = "material_supply" )

Delta m3 Sensitivitaet oben =
[Abgerechnete Menge m3]
    - CALCULATE ( SUM ( fact_delivery[delivered_m3_installed_low] ), fact_delivery[charge_type] = "material_supply" )

Deckungsgrad % =
DIVIDE ( [Abgerechnete Menge m3], [Liefermenge m3 eingebaut] )

Materialkosten EUR =
CALCULATE ( SUM ( fact_delivery[amount_eur] ), fact_delivery[charge_type] = "material_supply" )

Erloes Schotter EUR = SUM ( fact_lv_billing[billed_revenue_eur] )

Marge EUR = [Erloes Schotter EUR] - [Materialkosten EUR]

O Preis je t = DIVIDE ( [Materialkosten EUR], [Liefermenge t] )

Liefermenge kumuliert =
CALCULATE ( [Liefermenge t], FILTER ( ALLSELECTED ( dim_date[date] ), dim_date[date] <= MAX ( dim_date[date] ) ) )

Anteil Bereich an Gesamtmenge % =
DIVIDE ( [Liefermenge t], CALCULATE ( [Liefermenge t], ALL ( dim_area ) ) )

Offene Prueffaelle =
CALCULATE ( COUNTROWS ( fact_delivery ), fact_delivery[needs_review] = 1 )

Menge in Pruefung t =
CALCULATE ( [Liefermenge t], fact_delivery[needs_review] = 1 )

Anteil Menge in Pruefung % = DIVIDE ( [Menge in Pruefung t], [Liefermenge t] )
""",
        encoding="utf-8",
    )


def _write_report_structure(path: Path, ctx: Context) -> None:
    project = ctx.cfg["project"]["name"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""# Berichtsstruktur Power BI - {project}

Datenquelle: `gravel_model.xlsx`, je Blatt eine Tabelle (ListObject).
Der Pfad wird in Power BI als Parameter `DataFolder` angelegt, siehe METHOD.md.

## Seite 1 - Management Summary
- KPI Kacheln: Liefermenge t, Liefermenge m3 eingebaut, Abgerechnete Menge m3, Deckungsgrad %, Offene Prueffaelle
- Wasserfall: Liefermenge je Bereichsgruppe (Abschnitte, Querungen, nicht bereichsscharf)
- Hinweisbanner, solange das Ergebnis vorlaeufig ist (Anteil Menge in Pruefung %)

## Seite 2 - Bereiche im Detail
- Matrix Bereich x Material mit Liefermenge t und m3 eingebaut
- Balken Delta m3 je Bereich mit Fehlerbalken aus Sensitivitaet unten und oben
- Detailtabelle mit Drillthrough auf Belegebene (record_id, delivery_note_no, source_file)

## Seite 3 - Zeitverlauf
- Saeulen Liefermenge t je Monat, Linie kumuliert
- Kleine Vielfache je Material
- Filter auf Bereichsgruppe

## Seite 4 - Lieferanten und Preise
- Liefermenge je Lieferant und Transportart
- O Preis je t im Zeitverlauf (leer, solange keine Preisdaten vorliegen)
- Tabelle Zuschlagsarten, ausdruecklich getrennt von der Liefermenge

## Seite 5 - Datenqualitaet
Diese Seite ist nicht optional. Sie ist der Grund, warum das Management den Seiten eins bis vier glaubt.
- Anteil Menge in Pruefung % gegen Schwelle 5 Prozent
- Verteilung extraction_method und extraction_confidence
- Anteil der Menge ohne bereichsscharfe Buchung
- Umrechnungsbasis: Anteil der Menge mit conversion_confidence = assumption
- Liste der offenen Punkte aus DECISIONS.md
""",
        encoding="utf-8",
    )

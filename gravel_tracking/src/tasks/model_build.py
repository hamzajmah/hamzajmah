"""T5 Power BI Modell als Excel Arbeitsmappe (Sternschema)."""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

from ..areas import classify_area
from ..config import load_lv_mapping
from ..excel_out import verify_workbook, write_workbook
from ..harness import Context, TaskResult
from ..state import Task

FACT_DELIVERY = [
    "record_id", "delivery_date", "area_key", "material_key", "material_group", "supplier_key",
    "charge_type", "doc_type", "source_system", "source_file", "source_row_ref",
    "delivery_note_no", "delivery_note_source", "activity_id", "activity_text", "unload_location_text",
    "quantity", "unit", "quantity_t", "quantity_m3_doc",
    "delivered_m3_loose", "delivered_m3_installed", "delivered_m3_installed_low", "delivered_m3_installed_high",
    "conversion_confidence", "price_per_unit", "amount_eur",
    "extraction_method", "extraction_confidence", "is_duplicate", "needs_review", "review_reason",
    "location_label", "location_type", "location_from", "location_span_count",
]
FACT_LV_BILLING = ["lv_position_no", "billing_date", "period_month", "area_key", "material_group", "unit", "billed_quantity", "billed_value_eur", "lv_source"]
DIM_AREA = [
    "area_key", "area_name", "area_class", "area_group", "is_assigned",
    "section_key", "km_from", "km_to", "km_mid", "construction_method", "structure_name",
]
DIM_STRUCTURE = [
    "structure_name", "sequence", "section_key", "area_key", "km_from", "km_to", "km_mid",
    "length_m", "construction_method", "is_crossing", "crossing_no", "access_roads",
]
DIM_DATE = ["date", "year", "month", "year_month", "month_name_de", "quarter", "iso_week", "is_weekend"]
DIM_MATERIAL = ["material_key", "material_class", "grain_size", "rock_type", "is_gravel_supply", "bulk_density_t_per_m3", "installed_density_t_per_m3", "compaction_factor", "conversion_confidence", "conversion_source"]
DIM_SUPPLIER = ["supplier_key", "supplier_name", "records"]
DIM_LV_POSITION = ["lv_position_no", "short_text", "group_path", "area_key", "material_group", "unit", "contract_quantity", "billed_quantity_total", "unit_price_eur", "lv_source", "is_gravel_relevant"]
DIM_MATERIAL_GROUP = ["material_group", "label", "consumes_delivered_material", "fed_by_materials", "note"]
FACT_LOCATION_ALLOCATION = [
    "record_id", "location_point", "sp_number", "area_key", "material_group",
    "delivery_date", "allocated_t", "allocated_m3_installed", "allocation_method",
]
DIM_LOCATION = ["location_point", "sp_number", "location_kind", "sort_order"]
FACT_DELIVERY_LOG = [
    "period_month", "period_date", "location_point", "material_key", "material_group",
    "charge_type", "plant", "deliveries", "delivered_t", "source",
]

MONTHS_DE = ["Januar", "Februar", "Maerz", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]


def _material_key(material_class: str, grain_size: str) -> str:
    return f"{material_class or 'unbekannt'}|{grain_size or '-'}"


def _supplier_key(name: str) -> str:
    return (name or "unbekannt").strip()[:80]


def run(task: Task, ctx: Context) -> TaskResult:
    records = [r for r in ctx.store.records() if not r.is_duplicate]
    factors = (ctx.factors.get("factors") or {})
    mapping = load_lv_mapping(ctx.cfg)
    material_to_group = mapping.get("material_to_group") or {}

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
            rec.record_id, rec.delivery_date, area_key, material_key,
            material_to_group.get(f"{rec.material_class} {rec.grain_size}".strip(), "nicht_zugeordnet"), supplier_key,
            rec.charge_type, rec.doc_type, rec.source_system, rec.source_file, rec.source_row_ref,
            rec.delivery_note_no, rec.delivery_note_source, rec.activity_id, rec.activity_text, rec.unload_location_text,
            rec.quantity, rec.unit, rec.quantity_t, rec.quantity_m3_doc,
            rec.delivered_m3_loose, rec.delivered_m3_installed, rec.delivered_m3_installed_low, rec.delivered_m3_installed_high,
            rec.conversion_confidence, rec.price_per_unit, rec.amount_eur,
            rec.extraction_method, rec.extraction_confidence, rec.is_duplicate, rec.needs_review, rec.review_reason,
            rec.location_label or "ohne Ortsangabe", rec.location_type, rec.location_from, rec.location_span_count,
        ])

    lv_facts, lv_positions, lv_months = _read_lv_files(ctx.work_dir)
    structure_rows = _read_structures(ctx.work_dir)
    log_rows, log_points, log_months = _read_log_locations(ctx.work_dir)

    # dim_date muss jeden Schluessel beider Faktentabellen abdecken.
    start, end = ctx.cfg.period
    delivery_dates = [r.delivery_date for r in records if r.delivery_date]
    if delivery_dates:
        start = min(start, min(delivery_dates))
        end = max(end, max(delivery_dates))
    for months in (lv_months, log_months):
        if months:
            start = min(start, date.fromisoformat(min(months) + "-01"))
            end = max(end, date.fromisoformat(max(months) + "-01"))
    start = start.replace(day=1)
    end = _end_of_month(end)

    date_rows = []
    day = start
    while day <= end:
        date_rows.append([
            day, day.year, day.month, f"{day.year}-{day.month:02d}", MONTHS_DE[day.month - 1],
            f"Q{(day.month - 1) // 3 + 1}", int(day.strftime("%V")), 1 if day.weekday() >= 5 else 0,
        ])
        day += timedelta(days=1)

    # Bereiche, die nur auf der LV Seite vorkommen, gehoeren ebenfalls in
    # dim_area. Sonst fehlen Abschnitte und Muffengruben, in die noch nichts
    # geliefert wurde - und genau die will das Controlling sehen.
    area_patterns = ctx.cfg["areas"]["classes"]
    for area_key in [str(row[3]) for row in lv_facts] + [str(row[3]) for row in lv_positions]:
        if area_key and area_key not in areas:
            areas[area_key] = [classify_area(area_key, area_patterns)]
    area_rows = _area_rows(areas, structure_rows)

    group_rows = _material_group_rows(mapping, fact_rows, lv_facts)
    allocation_rows, location_rows = _read_location_files(ctx.work_dir)
    # Orte, die nur das Lieferlog kennt, gehoeren ebenfalls in die Dimension.
    known_points = {row[0] for row in location_rows}
    for point in sorted(log_points - known_points):
        kind = "Querungsbauwerk" if point.startswith("QR") else ("Setzpunkt" if point.startswith("SP") else "ohne Ortsangabe")
        digits = "".join(c for c in point if c.isdigit())
        location_rows.append([point, int(digits) if digits else None, kind, 0])
    location_rows.sort(key=lambda r: (r[2], r[1] if r[1] is not None else 0, r[0]))
    for index, entry in enumerate(location_rows):
        entry[3] = index

    tables = {
        "fact_delivery": (FACT_DELIVERY, fact_rows),
        "fact_lv_billing": (FACT_LV_BILLING, lv_facts),
        "dim_area": (DIM_AREA, area_rows),
        "dim_date": (DIM_DATE, date_rows),
        "dim_material": (DIM_MATERIAL, [materials[k] for k in sorted(materials)]),
        "dim_material_group": (DIM_MATERIAL_GROUP, group_rows),
        "dim_supplier": (DIM_SUPPLIER, [[k, k, suppliers[k]] for k in sorted(suppliers)]),
        "dim_lv_position": (DIM_LV_POSITION, lv_positions),
        "fact_location_allocation": (FACT_LOCATION_ALLOCATION, allocation_rows),
        "dim_location": (DIM_LOCATION, location_rows),
        "dim_structure": (DIM_STRUCTURE, structure_rows),
        "fact_delivery_log": (FACT_DELIVERY_LOG, log_rows),
    }

    workbook = ctx.cfg.root / ctx.cfg["powerbi"]["workbook"]
    write_workbook(workbook, tables)

    problems = verify_workbook(workbook, {name: cols for name, (cols, _) in tables.items()})
    problems += _referential_problems(fact_rows, area_rows, date_rows, materials, suppliers, start, end)
    problems += _lv_referential_problems(lv_facts, lv_positions, area_rows, date_rows, group_rows)
    problems += _location_referential_problems(allocation_rows, location_rows, area_rows, date_rows)
    point_keys = {row[0] for row in location_rows}
    group_keys = {row[0] for row in group_rows}
    date_keys = {row[0] for row in date_rows}
    for row in log_rows:
        if row[2] not in point_keys:
            problems.append(f"fk_log_ort_fehlt:{row[2]}")
        if row[4] not in group_keys:
            problems.append(f"fk_log_gruppe_fehlt:{row[4]}")
        if row[1] not in date_keys:
            problems.append(f"fk_log_datum_fehlt:{row[1]}")
    if problems:
        return TaskResult(ok=False, message="Modellpruefung: " + ";".join(sorted(set(problems))[:6]), error_class="model_invalid")

    _write_dax(ctx.output_dir / "powerbi" / "dax_measures.txt")
    _write_report_structure(ctx.output_dir / "powerbi" / "report_structure.md", ctx)

    ctx.log(f"MODEL fakten={len(fact_rows)} bereiche={len(area_rows)} material={len(materials)} datum={len(date_rows)}")
    return TaskResult(ok=True, message=f"{len(fact_rows)} Faktenzeilen", data={"facts": len(fact_rows), "areas": len(area_rows)})


def _area_rows(areas: dict[str, list[str]], structures: list[list]) -> list[list]:
    """Bereichsdimension mit Ortsbezug aus den Trassenstammdaten."""
    labels = {
        "section": "Trassenabschnitte", "crossing": "Querungen",
        "general": "Nicht bereichsscharf", "joint_pit": "Muffen- und Schubgruben",
    }
    # Kilometrierung je Sektion aus allen Bauwerksflaechen der Sektion.
    section_extent: dict[str, list[float]] = {}
    by_area: dict[str, list] = {}
    for row in structures:
        section_key, area_key = str(row[2]), str(row[3])
        km_from, km_to = row[4], row[5]
        if km_from is not None and km_to is not None:
            low, high = section_extent.get(section_key, [km_from, km_to])
            section_extent[section_key] = [min(low, km_from), max(high, km_to)]
        if area_key.startswith("QR") and area_key not in by_area:
            by_area[area_key] = row

    rows = []
    for key in sorted(areas):
        area_class = areas[key][0]
        section_key, km_from, km_to, method, structure = "", None, None, "", ""
        if area_class == "crossing" and key in by_area:
            structure_row = by_area[key]
            section_key, km_from, km_to = str(structure_row[2]), structure_row[4], structure_row[5]
            method, structure = str(structure_row[8]), str(structure_row[0])
        elif area_class == "section":
            section_key = key
            extent = section_extent.get(key)
            if extent:
                km_from, km_to = extent[0], extent[1]
            method = "gemischt"
        km_mid = round((km_from + km_to) / 2.0, 1) if km_from is not None and km_to is not None else None
        rows.append([
            key, key, area_class, labels.get(area_class, "Sonstige"),
            0 if area_class == "general" else 1,
            section_key, km_from, km_to, km_mid, method, structure,
        ])
    return rows


def _material_group_rows(mapping: dict, fact_rows: list[list], lv_facts: list[list]) -> list[list]:
    used = {str(row[4]) for row in fact_rows} | {str(row[4]) for row in lv_facts}
    groups = mapping.get("groups") or {}
    rows = []
    for key in sorted(used):
        entry = groups.get(key, {})
        rows.append([
            key,
            entry.get("label", "nicht zugeordnet" if key == "nicht_zugeordnet" else key),
            1 if entry.get("consumes_delivered_material") else 0,
            ", ".join(entry.get("fed_by_materials", [])),
            entry.get("note", ""),
        ])
    return rows


def _end_of_month(day: date) -> date:
    following = day.replace(day=28) + timedelta(days=4)
    return following.replace(day=1) - timedelta(days=1)


def _read_lv_files(work_dir: Path) -> tuple[list[list], list[list], list[str]]:
    """Liest die von T4 erzeugten LV Dateien in Fakten- und Dimensionszeilen."""
    facts: list[list] = []
    months: list[str] = []
    monthly_path = work_dir / "05_lv_billing_monthly.csv"
    if monthly_path.exists():
        with monthly_path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                month = row.get("period_month", "")
                if not month:
                    continue
                months.append(month)
                facts.append([
                    row.get("lv_position_no", ""), date.fromisoformat(f"{month}-01"), month,
                    row.get("area_key", ""), row.get("material_group", "") or "nicht_zugeordnet",
                    row.get("unit", ""), _num(row.get("billed_quantity")), _num(row.get("billed_value_eur")), "",
                ])

    dims: list[list] = []
    positions_path = work_dir / "05_lv_positions.csv"
    if positions_path.exists():
        with positions_path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                dims.append([
                    row.get("lv_position_no", ""), row.get("short_text", ""), row.get("group_path", ""),
                    row.get("area_key", ""), row.get("material_group", "") or "nicht_zugeordnet",
                    row.get("unit", ""), _num(row.get("contract_quantity")), _num(row.get("billed_quantity")),
                    _num(row.get("unit_price_eur")), row.get("lv_source", ""),
                    1 if row.get("is_gravel_relevant") == "true" else 0,
                ])
    return facts, dims, months


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
        if row[5] not in suppliers:
            problems.append(f"fk_supplier_fehlt:{row[5]}")
        if row[1] is not None and row[1] not in date_keys:
            problems.append(f"fk_datum_ausserhalb_dim_date:{row[1]}")
    expected_days = (end - start).days + 1
    if len(date_rows) != expected_days:
        problems.append("dim_date_unvollstaendig")
    return problems


def _read_location_files(work_dir: Path) -> tuple[list[list], list[list]]:
    """Ortsverteilung aus T3b. Punktdimension entsteht aus den Zuordnungen."""
    allocations: list[list] = []
    points: dict[str, list] = {}
    path = work_dir / "09_location_allocation.csv"
    if not path.exists():
        return [], []
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            day = row.get("delivery_date", "")
            point = row.get("location_point", "")
            number = _num(row.get("sp_number"))
            allocations.append([
                row.get("record_id", ""), point, int(number) if number is not None else None,
                row.get("area_final", ""), row.get("material_group", ""),
                date.fromisoformat(day) if day else None,
                _num(row.get("allocated_t")), _num(row.get("allocated_m3_installed")),
                row.get("allocation_method", ""),
            ])
            if point not in points:
                kind = "Querungsbauwerk" if point.startswith("QR") else "Setzpunkt"
                points[point] = [point, int(number) if number is not None else None, kind, len(points)]
    ordered = sorted(points.values(), key=lambda r: (r[2], r[1] if r[1] is not None else 0, r[0]))
    for index, entry in enumerate(ordered):
        entry[3] = index
    return allocations, ordered


def _read_structures(work_dir: Path) -> list[list]:
    path = work_dir / "01_structures.csv"
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            sequence = _num(row.get("sequence"))
            rows.append([
                row.get("structure_name", ""), int(sequence) if sequence is not None else None,
                row.get("section_key", ""), row.get("area_key", ""),
                _num(row.get("km_from")), _num(row.get("km_to")), _num(row.get("km_mid")),
                _num(row.get("length_m")), row.get("method", ""),
                1 if row.get("is_crossing") == "true" else 0,
                row.get("crossing_no", ""), row.get("access_roads", ""),
            ])
    return rows


def _read_log_locations(work_dir: Path) -> tuple[list[list], set[str], list[str]]:
    """Ortsauswertung des Lieferlogs, erzeugt von T4b."""
    path = work_dir / "13_delivery_log_by_location.csv"
    if not path.exists():
        return [], set(), []
    rows: list[list] = []
    points: set[str] = set()
    months: list[str] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            month = row.get("period_month", "")
            point = row.get("location_label", "") or "ohne Ortsangabe"
            if not month:
                continue
            months.append(month)
            points.add(point)
            deliveries = _num(row.get("deliveries"))
            rows.append([
                month, date.fromisoformat(f"{month}-01"), point,
                row.get("material_key", ""), row.get("material_group", "") or "nicht_zugeordnet",
                row.get("charge_type", ""), row.get("plant", ""),
                int(deliveries) if deliveries is not None else None,
                _num(row.get("delivered_t")), row.get("source", "supplier_log"),
            ])
    return rows, points, months


def _location_referential_problems(allocations, locations, area_rows, date_rows) -> list[str]:
    problems = []
    point_keys = {r[0] for r in locations}
    area_keys = {r[0] for r in area_rows}
    date_keys = {r[0] for r in date_rows}
    for row in allocations:
        if row[1] not in point_keys:
            problems.append(f"fk_ort_fehlt:{row[1]}")
        if row[3] and row[3] not in area_keys:
            problems.append(f"fk_ort_area_fehlt:{row[3]}")
        if row[5] is not None and row[5] not in date_keys:
            problems.append(f"fk_ort_datum_fehlt:{row[5]}")
    return problems


def _lv_referential_problems(lv_facts, lv_positions, area_rows, date_rows, group_rows) -> list[str]:
    problems = []
    area_keys = {r[0] for r in area_rows}
    date_keys = {r[0] for r in date_rows}
    group_keys = {r[0] for r in group_rows}
    position_keys = {r[0] for r in lv_positions}
    for row in lv_facts:
        if row[0] not in position_keys:
            problems.append(f"fk_lv_position_fehlt:{row[0]}")
        if row[3] and row[3] not in area_keys:
            problems.append(f"fk_lv_area_fehlt:{row[3]}")
        if row[4] not in group_keys:
            problems.append(f"fk_lv_gruppe_fehlt:{row[4]}")
        if row[1] not in date_keys:
            problems.append(f"fk_lv_datum_fehlt:{row[1]}")
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

// Die abgerechnete Menge kommt aus der Leistungsmeldung. Die Monatsverteilung
// ist aus Wochenwert geteilt durch Einheitspreis abgeleitet, siehe METHOD.md.
Abgerechnete Menge m3 = SUM ( fact_lv_billing[billed_quantity] )

Abgerechneter Wert EUR = SUM ( fact_lv_billing[billed_value_eur] )

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

Erloes Schotter EUR = SUM ( fact_lv_billing[billed_value_eur] )

Marge EUR = [Erloes Schotter EUR] - [Materialkosten EUR]

O Preis je t = DIVIDE ( [Materialkosten EUR], [Liefermenge t] )

Liefermenge kumuliert =
CALCULATE ( [Liefermenge t], FILTER ( ALLSELECTED ( dim_date[date] ), dim_date[date] <= MAX ( dim_date[date] ) ) )

Anteil Bereich an Gesamtmenge % =
DIVIDE ( [Liefermenge t], CALCULATE ( [Liefermenge t], ALL ( dim_area ) ) )

Vertragsmenge m3 = SUM ( dim_lv_position[contract_quantity] )

Restmenge LV m3 = [Vertragsmenge m3] - [Abgerechnete Menge m3]

// Ortsauswertung. allocation_method trennt belegte von verteilter Menge.
Liefermenge t je Ort belegt =
CALCULATE ( SUM ( fact_location_allocation[allocated_t] ), fact_location_allocation[allocation_method] = "exact" )

Liefermenge t je Ort aus Spannen verteilt =
CALCULATE ( SUM ( fact_location_allocation[allocated_t] ), fact_location_allocation[allocation_method] = "even_split" )

Liefermenge t je Ort gesamt = SUM ( fact_location_allocation[allocated_t] )

Anteil Menge ohne Ortsangabe % =
DIVIDE (
    CALCULATE ( [Liefermenge t], fact_delivery[location_type] = "none" ),
    [Liefermenge t]
)

// Zweitquelle Lieferlog. Nie mit der ERP Menge addieren: beide beschreiben in
// ihrem Ueberlappungszeitraum dieselben Fuhren.
Liefermenge t laut Lieferlog =
CALCULATE ( SUM ( fact_delivery_log[delivered_t] ), fact_delivery_log[charge_type] = "material_supply" )

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

## Beziehungen im Sternschema

| von | nach | Kardinalitaet | Richtung |
|---|---|---|---|
| fact_delivery[area_key] | dim_area[area_key] | n:1 | einfach |
| fact_delivery[delivery_date] | dim_date[date] | n:1 | einfach |
| fact_delivery[material_key] | dim_material[material_key] | n:1 | einfach |
| fact_delivery[material_group] | dim_material_group[material_group] | n:1 | einfach |
| fact_delivery[supplier_key] | dim_supplier[supplier_key] | n:1 | einfach |
| fact_lv_billing[area_key] | dim_area[area_key] | n:1 | einfach |
| fact_lv_billing[billing_date] | dim_date[date] | n:1 | einfach |
| fact_lv_billing[material_group] | dim_material_group[material_group] | n:1 | einfach |
| fact_lv_billing[lv_position_no] | dim_lv_position[lv_position_no] | n:1 | einfach |

dim_area, dim_date und dim_material_group filtern beide Faktentabellen. Genau
darueber entsteht der Vergleich Lieferung gegen Abrechnung im selben Visual.

## Seite 1 - Management Summary
- KPI Kacheln: Liefermenge t, Liefermenge m3 eingebaut, Abgerechnete Menge m3, Deckungsgrad %, Offene Prueffaelle
- Datumsfilter auf den gemeinsamen Zeitraum von Lieferung und Abrechnung setzen, sonst vergleicht der Bericht
  Zeitraeume, in denen nur eine Seite Daten hat
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

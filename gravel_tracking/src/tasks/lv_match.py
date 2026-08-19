"""T4 Abgleich Lieferung gegen Leistungsverzeichnis.

Der Vergleich erfolgt ausschliesslich gegen delivered_m3_installed, also gegen
das verdichtete Einbauvolumen. Fachliche Einordnungen von Deltas sind
ausdruecklich Hypothesen und stehen im Bericht, nicht in den Zahlen.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from ..config import load_lv_mapping
from ..decisions import Decision
from ..harness import Context, TaskResult
from ..lv_reader import LvPosition, apply_mapping, read_leistungsmeldung
from ..materials import CHARGE_SUPPLY
from ..state import Task

LV_COLUMNS = [
    "lv_position_no", "lv_source", "group_path", "short_text", "unit",
    "contract_quantity", "billed_quantity", "progress_pct", "unit_price_eur",
    "contract_value_eur", "billed_value_eur", "area_key", "material_group",
    "is_gravel_relevant", "quantity_comparable", "timeline_matches_total",
]
LV_MONTHLY_COLUMNS = ["lv_position_no", "area_key", "material_group", "unit", "period_month", "billed_quantity", "billed_value_eur"]
COMPARISON_COLUMNS = [
    "area_final", "area_class", "period_month", "material_group",
    "delivered_t", "delivered_m3_loose", "delivered_m3_installed",
    "delivered_m3_installed_low", "delivered_m3_installed_high",
    "billed_m3", "delta_m3", "delta_m3_low", "delta_m3_high", "coverage_pct",
    "billed_revenue_eur", "records", "records_in_review", "conversion_confidence",
    "in_comparison_period", "data_basis",
]


def _apply_area_fallback(positions: list[LvPosition], mapping: dict) -> int:
    rules = (mapping.get("area_fallback") or {}).get("rules", [])
    touched = 0
    for position in positions:
        if position.area_key:
            continue
        for rule in rules:
            if not re.search(rule["match_group_path"], position.group_path, re.IGNORECASE):
                continue
            extract = rule.get("extract_area_from_path")
            if extract:
                found = re.search(extract, position.group_path, re.IGNORECASE)
                if not found:
                    continue
                token = found.group(1)
                if rule.get("normalize") == "alnum_upper":
                    # "M-C33-007-V0" und "MC33007V0" sind derselbe Schluessel.
                    token = re.sub(r"[^A-Za-z0-9]", "", token).upper()
                position.area_key = rule["area_key_template"].format(token)
            else:
                position.area_key = rule["area_key"]
            touched += 1
            break
    return touched


def _read_lv(ctx: Context) -> tuple[list[LvPosition], dict]:
    path = ctx.cfg.path("lv_file")
    if path is None or not path.is_file():
        return [], {}
    mapping = load_lv_mapping(ctx.cfg)
    positions = read_leistungsmeldung(path, ctx.cfg.get("lv", {}).get("sheets", []))
    apply_mapping(positions, mapping)
    _apply_area_fallback(positions, mapping)
    return positions, mapping


def run(task: Task, ctx: Context) -> TaskResult:
    positions, mapping = _read_lv(ctx)
    _write_lv_files(ctx, positions)

    if not positions:
        ctx.decisions.add(Decision(
            category=3,
            topic="Leistungsverzeichnis und Aufmassstand liegen nicht vor",
            detail="In config/config.yaml ist unter paths.lv_file keine lesbare Datei hinterlegt.",
            impact="Die Spalten billed_m3, delta_m3, coverage_pct und Erloes bleiben leer. Die Auswertung zeigt die Lieferseite, nicht den Abgleich.",
            proposal="Leistungsmeldung bereitstellen und Pfad in config.yaml eintragen.",
            evidence="config/config.yaml, paths.lv_file",
        ))
    else:
        _add_lv_decisions(ctx, positions, mapping)

    material_to_group = (mapping.get("material_to_group") or {}) if mapping else {}
    delivered = _aggregate_delivery(ctx, material_to_group)
    billed = _aggregate_billing(positions)

    period_start, period_end = ctx.cfg.period
    rows = _build_comparison(delivered, billed, bool(positions), period_start.strftime("%Y-%m"), period_end.strftime("%Y-%m"))
    _write_csv(ctx.work_dir / "06_comparison.csv", COMPARISON_COLUMNS, rows)

    # Abnahme: jede Zeile hat Bereich, Periode und eine nachvollziehbare Basis.
    bad = [r for r in rows if not r["area_final"] or not r["period_month"] or not r["data_basis"]]
    if bad:
        return TaskResult(ok=False, message=f"{len(bad)} Vergleichszeilen ohne Bereich, Periode oder Basis", error_class="comparison_incomplete")

    gravel = [p for p in positions if p.is_gravel_relevant]
    ctx.log(f"LV_MATCH vergleichszeilen={len(rows)} lv_positionen={len(positions)} davon_schotterrelevant={len(gravel)}")
    return TaskResult(ok=True, message=f"{len(rows)} Vergleichszeilen", data={"rows": len(rows), "lv_positions": len(positions), "gravel_positions": len(gravel)})


def _aggregate_delivery(ctx: Context, material_to_group: dict[str, str]) -> dict[tuple, dict[str, float]]:
    buckets: dict[tuple, dict[str, float]] = {}
    for rec in ctx.store.records():
        if rec.charge_type != CHARGE_SUPPLY or rec.is_duplicate:
            continue
        material_key = f"{rec.material_class} {rec.grain_size}".strip()
        group = material_to_group.get(material_key, "")
        period = rec.delivery_date.strftime("%Y-%m") if rec.delivery_date else ""
        key = (rec.area_final, rec.area_class, period, group)
        b = buckets.setdefault(key, {
            "delivered_t": 0.0, "delivered_m3_loose": 0.0, "delivered_m3_installed": 0.0,
            "delivered_m3_installed_low": 0.0, "delivered_m3_installed_high": 0.0,
            "records": 0.0, "records_in_review": 0.0, "assumption": 0.0,
        })
        b["delivered_t"] += rec.quantity_t or 0.0
        b["delivered_m3_loose"] += rec.delivered_m3_loose or 0.0
        b["delivered_m3_installed"] += rec.delivered_m3_installed or 0.0
        b["delivered_m3_installed_low"] += rec.delivered_m3_installed_low or 0.0
        b["delivered_m3_installed_high"] += rec.delivered_m3_installed_high or 0.0
        b["records"] += 1.0
        b["records_in_review"] += 1.0 if rec.needs_review else 0.0
        b["assumption"] += 1.0 if rec.conversion_confidence == "assumption" else 0.0
    return buckets


def _aggregate_billing(positions: list[LvPosition]) -> dict[tuple, dict[str, float]]:
    billed: dict[tuple, dict[str, float]] = {}
    for position in positions:
        if not position.is_gravel_relevant or not position.area_key or not position.quantity_comparable:
            continue
        for month, quantity in position.monthly.items():
            key = (position.area_key, month, position.material_group)
            b = billed.setdefault(key, {"billed": 0.0, "revenue": 0.0})
            b["billed"] += quantity
            b["revenue"] += quantity * (position.unit_price_eur or 0.0)
    return billed


def _build_comparison(
    delivered: dict[tuple, dict[str, float]],
    billed: dict[tuple, dict[str, float]],
    has_lv: bool,
    period_from: str,
    period_to: str,
) -> list[dict[str, object]]:
    keys = {(a, ac, m, g) for (a, ac, m, g) in delivered}
    known_class = {a: ac for (a, ac, _, _) in delivered}
    for (area, month, group) in billed:
        keys.add((area, known_class.get(area, "unknown"), month, group))

    rows: list[dict[str, object]] = []
    for area, area_class, month, group in sorted(keys):
        d = delivered.get((area, area_class, month, group), {})
        b = billed.get((area, month, group), {})
        installed = round(d.get("delivered_m3_installed", 0.0), 2)
        low = round(d.get("delivered_m3_installed_low", 0.0), 2)
        high = round(d.get("delivered_m3_installed_high", 0.0), 2)
        billed_m3 = round(b.get("billed", 0.0), 2) if has_lv else None
        delta = round(billed_m3 - installed, 2) if billed_m3 is not None else None
        coverage = round(100.0 * billed_m3 / installed, 2) if billed_m3 is not None and installed else None

        if not group:
            basis = "Material keiner LV Gruppe zugeordnet"
        elif not has_lv:
            basis = "Lieferseite belegt, LV Seite offen"
        elif installed and billed_m3:
            basis = "Lieferung gegen LV"
        elif installed:
            basis = "nur Lieferung, keine Abrechnung in dieser Periode"
        else:
            basis = "nur Abrechnung, keine Lieferung in dieser Periode"

        rows.append({
            "in_comparison_period": "true" if period_from <= month <= period_to else "false",
            "area_final": area, "area_class": area_class, "period_month": month, "material_group": group or "nicht_zugeordnet",
            "delivered_t": round(d.get("delivered_t", 0.0), 2),
            "delivered_m3_loose": round(d.get("delivered_m3_loose", 0.0), 2),
            "delivered_m3_installed": installed,
            "delivered_m3_installed_low": low,
            "delivered_m3_installed_high": high,
            "billed_m3": billed_m3,
            "delta_m3": delta,
            "delta_m3_low": round(billed_m3 - high, 2) if billed_m3 is not None else None,
            "delta_m3_high": round(billed_m3 - low, 2) if billed_m3 is not None else None,
            "coverage_pct": coverage,
            "billed_revenue_eur": round(b.get("revenue", 0.0), 2) if has_lv else None,
            "records": int(d.get("records", 0.0)),
            "records_in_review": int(d.get("records_in_review", 0.0)),
            "conversion_confidence": "assumption" if d.get("assumption", 0.0) else ("" if not d else "measured"),
            "data_basis": basis,
        })
    return rows


def _write_lv_files(ctx: Context, positions: list[LvPosition]) -> None:
    _write_csv(ctx.work_dir / "05_lv_positions.csv", LV_COLUMNS, [
        {
            "lv_position_no": p.lv_position_no, "lv_source": p.lv_source, "group_path": p.group_path,
            "short_text": p.short_text, "unit": p.unit, "contract_quantity": p.contract_quantity,
            "billed_quantity": p.billed_quantity, "progress_pct": p.progress_pct,
            "unit_price_eur": p.unit_price_eur, "contract_value_eur": p.contract_value_eur,
            "billed_value_eur": p.billed_value_eur, "area_key": p.area_key,
            "material_group": p.material_group, "is_gravel_relevant": "true" if p.is_gravel_relevant else "false",
            "quantity_comparable": "true" if p.quantity_comparable else "false",
            "timeline_matches_total": "true" if p.timeline_matches_total else "false",
        }
        for p in positions
    ])
    _write_csv(ctx.work_dir / "05_lv_billing_monthly.csv", LV_MONTHLY_COLUMNS, [
        {
            "lv_position_no": p.lv_position_no, "area_key": p.area_key, "material_group": p.material_group,
            "unit": p.unit, "period_month": month, "billed_quantity": round(quantity, 3),
            "billed_value_eur": round(quantity * (p.unit_price_eur or 0.0), 2),
        }
        for p in positions if p.is_gravel_relevant
        for month, quantity in sorted(p.monthly.items())
    ])


def _add_lv_decisions(ctx: Context, positions: list[LvPosition], mapping: dict) -> None:
    gravel = [p for p in positions if p.is_gravel_relevant]
    if mapping.get("status") == "vorschlag":
        groups = sorted({p.material_group for p in gravel})
        ctx.decisions.add(Decision(
            category=3,
            topic="Zuordnung LV Position zu geliefertem Material ist noch nicht freigegeben",
            detail=(
                f"{len(gravel)} LV Positionen sind ueber config/lv_mapping.yaml den Gruppen {groups} zugeordnet. "
                "Die Datei traegt status: vorschlag."
            ),
            impact="Diese Zuordnung bestimmt, gegen welche LV Menge eine Lieferung verglichen wird, und damit jedes Delta.",
            proposal="Zuordnung fachlich pruefen, danach in config/lv_mapping.yaml status: freigegeben mit Namen und Datum eintragen.",
            evidence="config/lv_mapping.yaml, work/05_lv_positions.csv",
        ))

    fallback_used = [p for p in gravel if p.area_key in ("GENERAL", "SCHUBGRUB")]
    if fallback_used and (mapping.get("area_fallback") or {}).get("status") == "vorschlag":
        ctx.decisions.add(Decision(
            category=3,
            topic="LV Kapitel ohne Bereich werden dem Sammelbereich GENERAL zugeordnet",
            detail=(
                f"{len(fallback_used)} LV Positionen aus Kapiteln ohne Bereichsangabe (Baustrassen, Zufahrten, "
                "BE Flaechen, Schubgruben) werden dem IFS Sammelbereich GENERAL gegenuebergestellt."
            ),
            impact="Ohne diese Annahme haetten die auf GENERAL gebuchten Lieferungen keine Gegenposition und das Delta waere kuenstlich hoch.",
            proposal="Bestaetigen, dass die auf GENERAL gebuchten Lieferungen den Baustrassen, Zufahrten und BE Flaechen entsprechen.",
            evidence="config/lv_mapping.yaml, area_fallback",
        ))

    broken = [p for p in gravel if not p.timeline_matches_total]
    if broken:
        sample = ", ".join(f"{p.lv_position_no} ({p.short_text[:40]})" for p in broken[:5])
        ctx.decisions.add(Decision(
            category=6,
            topic="Wochenwerte und abgerechnete Menge passen bei einzelnen LV Positionen nicht zusammen",
            detail=(
                f"Bei {len(broken)} schotterrelevanten Positionen ergibt die Summe der Wochenwerte geteilt durch den "
                f"Einheitspreis nicht die Spalte RE MENGE. Betroffen: {sample}."
            ),
            impact="Die monatliche Verteilung der abgerechneten Menge ist fuer diese Positionen unsicher. Die Gesamtmenge bleibt die aus RE MENGE.",
            proposal="Pruefen, ob die Leistung zwischen zwei Positionen umgebucht wurde, ohne die Wochenwerte mitzunehmen.",
            evidence="work/05_lv_positions.csv, Spalte timeline_matches_total",
        ))

    unit_mismatch = sorted({p.unit for p in gravel if p.unit not in (ctx.cfg.get("lv", {}).get("comparable_units") or ["m3"])})
    if unit_mismatch:
        ctx.decisions.add(Decision(
            category=6,
            topic="Schotterrelevante LV Positionen mit abweichender Einheit",
            detail=f"Einheiten ausserhalb des Mengenvergleichs: {unit_mismatch}.",
            impact="Positionen in diesen Einheiten lassen sich nicht direkt gegen Kubikmeter stellen.",
            proposal="Umrechnungsregel je Einheit festlegen oder die Position aus dem Vergleich nehmen.",
            evidence="work/05_lv_positions.csv",
        ))

    months = sorted({m for p in gravel for m in p.monthly})
    period_from, period_to = (d.strftime("%Y-%m") for d in ctx.cfg.period)
    outside = [m for m in months if m < period_from or m > period_to]
    if outside:
        billed_outside = sum(
            q for p in gravel for m, q in p.monthly.items() if m < period_from or m > period_to
        )
        ctx.decisions.add(Decision(
            category=6,
            topic="Vergleichszeitraum von Lieferung und Abrechnung deckt sich nicht",
            detail=(
                f"Die Leistungsmeldung weist schotterrelevante Mengen ab {months[0]} aus, die vorliegenden Lieferdaten "
                f"beginnen erst {period_from}. Ausserhalb des Vergleichszeitraums liegen {billed_outside:,.0f} m3 "
                "abgerechnete Menge."
            ).replace(",", "."),
            impact="Ein Vergleich ueber den gesamten Bauzeitraum wuerde ein Delta zeigen, das nur aus dem fehlenden Datenbestand entsteht.",
            proposal=(
                "Entweder ERP Wareneingaenge ab Baubeginn nachliefern, oder den Vergleich ausdruecklich auf den "
                "gemeinsamen Zeitraum begrenzen. Die Spalte in_comparison_period in work/06_comparison.csv trennt beides."
            ),
            evidence="work/06_comparison.csv, Spalte in_comparison_period",
        ))

    ctx.decisions.add(Decision(
        category=6,
        topic="Lieferdaten decken nur eine Bestellung eines Lieferanten ab",
        detail=(
            "Der ausgewertete IFS Wareneingang gehoert zur Bestellung P100042563 des Lieferanten Baustoff Vertrieb "
            "Fulda Werra. Ob weitere Lieferanten oder Bestellungen Schotter in dieselben LV Positionen geliefert haben, "
            "ist aus diesem Bestand nicht erkennbar."
        ),
        impact="Fehlt eine zweite Quelle, erscheint die gelieferte Menge zu niedrig und das Delta gegen das LV zu hoch.",
        proposal="Bestaetigen, dass P100042563 die einzige Schotterquelle ist, sonst weitere Wareneingangsexporte bereitstellen.",
        evidence="work/00_inventory.csv",
    ))

    # Wo bucht das LV, wo bucht das ERP? Ein Vergleich je Bereich setzt voraus,
    # dass beide Seiten denselben Bereich kennen.
    for group in sorted({p.material_group for p in gravel}):
        billed_areas = {p.area_key for p in gravel if p.material_group == group and (p.billed_quantity or 0) > 0}
        if billed_areas and billed_areas <= {"GENERAL", "SCHUBGRUB", ""}:
            ctx.decisions.add(Decision(
                category=3,
                topic=f"LV fuehrt die Gruppe {group} nur projektweit, das ERP bucht je Bereich",
                detail=(
                    f"Die abgerechneten Mengen der Gruppe {group} stehen ausschliesslich in Kapiteln ohne Bereichsbezug "
                    "(Baustrassen, Zufahrten, BE Flaechen, Abspulplaetze). Die Lieferungen sind dagegen auf Abschnitte "
                    "und Querungen gebucht."
                ),
                impact="Ein Vergleich je Bereich ist fuer diese Gruppe nicht moeglich, nur auf Projektebene.",
                proposal=(
                    "Entweder im LV eine bereichsscharfe Aufmassfuehrung anlegen, oder den Vergleich dieser Gruppe "
                    "bewusst nur auf Projektebene fuehren."
                ),
                evidence="work/05_lv_positions.csv, Spalte area_key",
            ))

    t_positions = [p for p in gravel if p.unit == "t"]
    if t_positions:
        ctx.decisions.add(Decision(
            category=3,
            topic="LV Position Sandabdeckung rechnet in Tonnen",
            detail=f"{len(t_positions)} Positionen der Gruppe sand_cover fuehren Tonnen statt Kubikmeter.",
            impact="Der Vergleich gegen delivered_m3_installed passt hier nicht; die Menge wird derzeit nicht gegengestellt.",
            proposal="Sandabdeckung direkt in Tonnen vergleichen, dafuer eine eigene Vergleichszeile freigeben.",
            evidence="work/05_lv_positions.csv, unit = t",
        ))


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, delimiter=";", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})

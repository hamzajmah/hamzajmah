"""T4b Gegenprobe gegen das Lieferlog des Lieferanten.

Das Lieferlog ist eine zweite, unabhaengig gefuehrte Aufzeichnung derselben
Lieferungen: je Lieferschein eine Zeile mit Datum, Gemisch, Menge, Werk und
Abladestelle. Es wird ausdruecklich **nicht** in die Liefermenge gemischt.
Es dient der Kontrolle: stimmen Menge, Zeitraum und Ort ueberein?
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..config import load_lv_mapping
from ..decisions import Decision
from ..harness import Context, TaskResult
from ..locations import parse as parse_location
from ..materials import CHARGE_SUPPLY, classify
from ..state import Task

MONTH_COLUMNS = [
    "period_month", "log_deliveries", "log_t", "erp_deliveries", "erp_t",
    "difference_t", "difference_pct", "coverage_note",
]
LOCATION_COLUMNS = ["location_label", "location_type", "log_t", "erp_t", "difference_t", "coverage_note"]
LOG_LOCATION_COLUMNS = [
    "period_month", "location_label", "location_type", "sp_number",
    "material_key", "material_group", "charge_type", "plant",
    "deliveries", "delivered_t", "first_delivery", "last_delivery", "source",
]
_LS = re.compile(r"(\d{4,})")


def _quantity(value: Any) -> float | None:
    if value is None or value != value:
        return None
    text = str(value).strip().lower().replace("t", "").replace(" ", "")
    text = text.replace(".", "") if text.count(",") == 1 and text.count(".") >= 1 else text
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _read_log(path: Path, sheet: str) -> list[dict[str, Any]]:
    import pandas as pd

    frame = pd.read_excel(path, sheet_name=sheet)
    columns = {str(c).strip().rstrip(":").strip().upper(): c for c in frame.columns}
    rows = []
    for row in frame.to_dict("records"):
        day = _day(row.get(columns.get("DAUM", columns.get("DATUM", "")), None))
        quantity = _quantity(row.get(columns.get("MENGE IN", ""), None))
        if day is None or quantity is None:
            continue
        place_text = str(row.get(columns.get("BAUSELLE", columns.get("BAUSTELLE", "")), "") or "")
        note = str(row.get(columns.get("LIEFERSCHEIN NR", ""), "") or "")
        ls = _LS.search(note)
        material_text = str(row.get(columns.get("GEMISCH", ""), "") or "").strip()
        rows.append({
            "day": day, "t": quantity, "material": material_text, "info": classify(material_text),
            "place_text": place_text, "place": parse_location(place_text),
            "plant": str(row.get(columns.get("WERK", ""), "") or "").strip(),
            "ls": ls.group(1) if ls else "",
        })
    return rows


def run(task: Task, ctx: Context) -> TaskResult:
    path = ctx.cfg.path("supplier_log")
    if path is None or not path.is_file():
        return TaskResult(ok=True, message="kein Lieferlog hinterlegt", data={"log_rows": 0})

    spec = ctx.cfg.get("supplier_log", {}) or {}
    log = _read_log(path, spec.get("sheet", "Baustoffgemisch"))
    records = [r for r in ctx.store.records() if r.charge_type == CHARGE_SUPPLY and not r.is_duplicate]

    log_month: dict[str, list[float]] = defaultdict(list)
    erp_month: dict[str, list[float]] = defaultdict(list)
    for row in log:
        log_month[row["day"].strftime("%Y-%m")].append(row["t"])
    for rec in records:
        if rec.delivery_date:
            erp_month[rec.delivery_date.strftime("%Y-%m")].append(rec.quantity_t or 0.0)

    months = sorted(set(log_month) | set(erp_month))
    month_rows = []
    for month in months:
        log_t = round(sum(log_month.get(month, [])), 2)
        erp_t = round(sum(erp_month.get(month, [])), 2)
        both = bool(log_month.get(month)) and bool(erp_month.get(month))
        note = "beide Quellen" if both else ("nur Lieferlog" if log_month.get(month) else "nur ERP")
        month_rows.append({
            "period_month": month, "log_deliveries": len(log_month.get(month, [])), "log_t": log_t,
            "erp_deliveries": len(erp_month.get(month, [])), "erp_t": erp_t,
            "difference_t": round(log_t - erp_t, 2),
            "difference_pct": round(100.0 * (log_t - erp_t) / erp_t, 1) if erp_t else "",
            "coverage_note": note,
        })
    _write(ctx.work_dir / "10_reconciliation_by_month.csv", MONTH_COLUMNS, month_rows)

    # Ortsvergleich nur dort, wo beide Quellen Daten haben.
    overlap = {m for m in months if log_month.get(m) and erp_month.get(m)}
    log_place: dict[tuple[str, str], float] = defaultdict(float)
    erp_place: dict[tuple[str, str], float] = defaultdict(float)
    for row in log:
        if row["day"].strftime("%Y-%m") in overlap:
            place = row["place"]
            log_place[(place.location_label or "ohne Ortsangabe", place.location_type)] += row["t"]
    for rec in records:
        if rec.delivery_date and rec.delivery_date.strftime("%Y-%m") in overlap:
            erp_place[(rec.location_label or "ohne Ortsangabe", rec.location_type)] += rec.quantity_t or 0.0

    location_rows = []
    for key in sorted(set(log_place) | set(erp_place)):
        log_t = round(log_place.get(key, 0.0), 2)
        erp_t = round(erp_place.get(key, 0.0), 2)
        location_rows.append({
            "location_label": key[0], "location_type": key[1], "log_t": log_t, "erp_t": erp_t,
            "difference_t": round(log_t - erp_t, 2),
            "coverage_note": "beide Quellen" if log_t and erp_t else ("nur Lieferlog" if log_t else "nur ERP"),
        })
    _write(ctx.work_dir / "11_reconciliation_by_location.csv", LOCATION_COLUMNS, location_rows)

    log_total = round(sum(r["t"] for r in log), 2)
    erp_total = round(sum(r.quantity_t or 0.0 for r in records), 2)
    log_only_months = sorted(m for m in months if log_month.get(m) and not erp_month.get(m))
    log_only_t = round(sum(sum(log_month[m]) for m in log_only_months), 2)
    plants = sorted({r["plant"] for r in log if r["plant"]})
    ls_overlap = len({r["ls"] for r in log if r["ls"]} & {r.delivery_note_no for r in records if r.delivery_note_source == "document_note"})

    if log_only_t:
        ctx.decisions.add(Decision(
            category=3,
            topic="Lieferungen vor dem Beginn des ERP Bestands",
            detail=(
                f"Das Lieferlog weist in {len(log_only_months)} Monaten ohne ERP Daten {log_only_t:,.0f} t aus "
                f"(frueheste Lieferung {months[0]})."
            ).replace(",", "."),
            impact=(
                "Die ausgewertete Liefermenge beginnt erst mit dem ERP Bestand. Jeder Vergleich gegen die "
                "Leistungsmeldung, die frueher startet, ist deshalb unvollstaendig."
            ),
            proposal="ERP Wareneingaenge ab Baubeginn nachliefern, sonst den Vergleichszeitraum ausdruecklich begrenzen.",
            evidence="work/10_reconciliation_by_month.csv",
        ))

    if len(plants) > 1:
        ctx.decisions.add(Decision(
            category=6,
            topic="Das Lieferlog nennt mehrere Lieferwerke",
            detail=(
                f"Im Lieferlog stehen {len(plants)} Werksbezeichnungen (teils Schreibvarianten desselben Werks): "
                f"{', '.join(plants[:8])}{' ...' if len(plants) > 8 else ''}. Im Ueberlappungszeitraum decken sich die "
                "Mengen beider Quellen weitgehend, dort gehoeren die Werke also zur ausgewerteten Bestellung."
            ),
            impact=(
                "Ob die Werke ausserhalb des Ueberlappungszeitraums ebenfalls zu dieser Bestellung gehoeren oder zu "
                "weiteren Bestellungen, ist aus dem Log nicht ableitbar: es fuehrt kein Lieferantenfeld."
            ),
            proposal="In IFS pruefen, welche Bestellungen mit Schotterbezug es neben P100042563 gibt.",
            evidence="work/10_reconciliation_by_month.csv",
        ))

    if not ls_overlap:
        ctx.decisions.add(Decision(
            category=6,
            topic="Lieferschein zu Lieferschein laesst sich nicht abgleichen",
            detail=(
                "Im Ueberlappungszeitraum tragen die ERP Zeilen keine Lieferscheinnummer im Notizfeld, das Lieferlog "
                "dagegen schon. Ein Abgleich Beleg gegen Beleg ist deshalb nicht moeglich, nur einer ueber Summen."
            ),
            impact="Doppelerfassungen oder fehlende Fuhren lassen sich nicht einzeln nachweisen.",
            proposal="Lieferscheinnummer im Wareneingang verbindlich erfassen, dann ist der Abgleich zeilenscharf.",
            evidence="work/11_reconciliation_by_location.csv",
        ))

    _write_log_locations(ctx, log)

    _add_location_quality_decision(ctx, log)

    ctx.log(
        f"RECONCILE log_zeilen={len(log)} log_t={log_total:.0f} erp_t={erp_total:.0f} "
        f"monate_nur_log={len(log_only_months)} werke={len(plants)} ls_treffer={ls_overlap}"
    )
    return TaskResult(ok=True, message=f"Lieferlog: {log_total:.0f} t, ERP: {erp_total:.0f} t", data={
        "log_rows": len(log), "log_t": log_total, "erp_t": erp_total,
        "months_log_only": log_only_months, "log_only_t": log_only_t,
        "plants": len(plants), "ls_matches": ls_overlap,
    })


def _add_location_quality_decision(ctx: Context, log: list[dict[str, Any]]) -> None:
    """Das Lieferlog ist ortsscharf, wo das ERP nur Spannen kennt."""
    supply = [r for r in log if r["info"].charge_type == CHARGE_SUPPLY]
    if not supply:
        return
    total = sum(r["t"] for r in supply)
    exact = sum(r["t"] for r in supply if r["place"].location_type in ("point", "crossing"))
    spans = sum(r["t"] for r in supply if r["place"].location_type == "span")
    erp = [r for r in ctx.store.records() if r.charge_type == CHARGE_SUPPLY and not r.is_duplicate]
    erp_total = sum(r.quantity_t or 0.0 for r in erp)
    erp_exact = sum(r.quantity_t or 0.0 for r in erp if r.location_type in ("point", "crossing"))

    ctx.decisions.add(Decision(
        category=6,
        topic="Das Lieferlog ist ortsscharfer als der Wareneingang",
        detail=(
            f"Im Lieferlog sind {100 * exact / total:.1f} Prozent der Menge einem einzelnen Setzpunkt oder "
            f"Querungsbauwerk zugeordnet und {100 * spans / total:.1f} Prozent einer Spanne. Im Wareneingang sind es "
            f"{100 * erp_exact / erp_total:.1f} Prozent punktscharf."
        ),
        impact=(
            "Fuer den Zeitraum vor dem ERP Bestand ist das Log die einzige Ortsquelle. Beide Quellen stehen im Modell "
            "getrennt (fact_delivery und fact_delivery_log) und duerfen nie addiert werden, weil sie sich im "
            "Ueberlappungszeitraum auf dieselben Fuhren beziehen."
        ),
        proposal=(
            "Fuer die Ortsauswertung vor 2026 das Lieferlog verwenden, ab 2026 den Wareneingang. Dauerhaft besser: "
            "die Ortsangabe im Wareneingang je Fuhre punktscharf erfassen."
        ),
        evidence="work/13_delivery_log_by_location.csv",
    ))


def _write_log_locations(ctx: Context, log: list[dict[str, Any]]) -> None:
    """Ortsauswertung des Lieferlogs als eigene Quelle.

    Das Log deckt einen Zeitraum ab, fuer den es keinen Wareneingangsexport
    gibt. Seine Ortsangaben sind dort die einzige Information, wohin geliefert
    wurde. Sie werden getrennt gefuehrt, nie in die ERP Menge gemischt.
    """
    material_to_group = load_lv_mapping(ctx.cfg).get("material_to_group") or {}
    buckets: dict[tuple, dict[str, Any]] = {}
    for row in log:
        info = row["info"]
        place = row["place"]
        material_key = f"{info.material_class} {info.grain_size}".strip()
        key = (
            row["day"].strftime("%Y-%m"),
            place.location_label or "ohne Ortsangabe",
            place.location_type,
            place.location_from,
            material_key,
            material_to_group.get(material_key, "nicht_zugeordnet"),
            info.charge_type,
            row["plant"],
        )
        bucket = buckets.setdefault(key, {"n": 0, "t": 0.0, "days": []})
        bucket["n"] = int(bucket["n"]) + 1
        bucket["t"] = float(bucket["t"]) + row["t"]
        bucket["days"].append(row["day"].isoformat())

    rows = []
    for key in sorted(buckets, key=lambda k: tuple("" if v is None else str(v) for v in k)):
        month, label, kind, number, material_key, group, charge, plant = key
        bucket = buckets[key]
        days = sorted(bucket["days"])
        rows.append({
            "period_month": month, "location_label": label, "location_type": kind,
            "sp_number": number, "material_key": material_key, "material_group": group,
            "charge_type": charge, "plant": plant, "deliveries": bucket["n"],
            "delivered_t": round(float(bucket["t"]), 2),
            "first_delivery": days[0], "last_delivery": days[-1],
            "source": "supplier_log",
        })
    _write(ctx.work_dir / "13_delivery_log_by_location.csv", LOG_LOCATION_COLUMNS, rows)


def _write(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, delimiter=";", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})

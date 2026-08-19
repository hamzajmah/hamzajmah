"""T3b Ortsauswertung.

Beantwortet die Frage, wohin wie viel geliefert wurde. Grundlage sind die
Ortsangaben aus dem Notizfeld des Wareneingangs.

Zwei Sichten, bewusst getrennt:

1. `07_delivery_by_location.csv` fuehrt jede Ortsangabe so, wie sie notiert
   wurde. Eine Spanne bleibt eine Spanne.
2. `08_location_points.csv` bricht auf den einzelnen Setzpunkt herunter. Dort
   steht die exakt zugeordnete Menge getrennt von der Menge, die nur ueber eine
   Gleichverteilung einer Spanne auf den Punkt entfaellt. Die Gleichverteilung
   ist eine Rechenannahme und wird nie mit der exakten Menge vermischt.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..config import load_lv_mapping
from ..decisions import Decision
from ..harness import Context, TaskResult
from ..locations import CROSSING, POINT, SPAN, point_label
from ..materials import CHARGE_SUPPLY
from ..state import Task

BY_LOCATION_COLUMNS = [
    "location_label", "location_type", "location_from", "location_to", "span_count",
    "area_final", "area_class", "material_group", "material_key", "period_month",
    "deliveries", "delivered_t", "delivered_m3_installed", "first_delivery", "last_delivery",
]
POINT_COLUMNS = [
    "location_point", "sp_number", "area_final", "material_group",
    "t_exact", "m3_exact", "deliveries_exact",
    "t_from_spans_even_split", "m3_from_spans_even_split", "spans_touching",
    "t_total_view", "allocation_note",
]
ALLOCATION_COLUMNS = [
    "record_id", "location_point", "sp_number", "area_final", "material_group",
    "delivery_date", "allocated_t", "allocated_m3_installed", "allocation_method",
]


@dataclass
class LocationBucket:
    """Summen je Ortsangabe, Bereich, Material und Monat."""

    location_from: int | None = None
    location_to: int | None = None
    span_count: int = 0
    area_class: str = ""
    deliveries: int = 0
    delivered_t: float = 0.0
    delivered_m3_installed: float = 0.0
    days: list[str] = field(default_factory=list)

    @property
    def first_delivery(self) -> str:
        return min(self.days) if self.days else ""

    @property
    def last_delivery(self) -> str:
        return max(self.days) if self.days else ""


def run(task: Task, ctx: Context) -> TaskResult:
    records = [r for r in ctx.store.records() if r.charge_type == CHARGE_SUPPLY and not r.is_duplicate]
    if not records:
        return TaskResult(ok=False, message="keine Lieferungen vorhanden", error_class="no_records", escalate=False)

    material_to_group = load_lv_mapping(ctx.cfg).get("material_to_group") or {}

    by_location: dict[tuple, LocationBucket] = {}
    allocations: list[dict[str, object]] = []
    points_exact: dict[tuple, dict[str, float]] = defaultdict(lambda: {"t": 0.0, "m3": 0.0, "n": 0.0})
    points_split: dict[tuple, dict[str, float]] = defaultdict(lambda: {"t": 0.0, "m3": 0.0, "spans": 0.0})

    total_t = 0.0
    located_t = 0.0
    by_type_t: dict[str, float] = defaultdict(float)

    for rec in records:
        quantity = rec.quantity_t or 0.0
        volume = rec.delivered_m3_installed or 0.0
        total_t += quantity
        by_type_t[rec.location_type] += quantity
        if rec.location_type != "none":
            located_t += quantity

        month = rec.delivery_date.strftime("%Y-%m") if rec.delivery_date else ""
        material_key = rec.material_key()
        group = material_to_group.get(material_key, "nicht_zugeordnet")
        label = rec.location_label or "ohne Ortsangabe"
        key = (label, rec.location_type, rec.area_final, group, material_key, month)
        bucket = by_location.setdefault(key, LocationBucket(
            location_from=rec.location_from, location_to=rec.location_to,
            span_count=rec.location_span_count, area_class=rec.area_class,
        ))
        bucket.deliveries += 1
        bucket.delivered_t += quantity
        bucket.delivered_m3_installed += volume
        day = rec.delivery_date.isoformat() if rec.delivery_date else ""
        if day:
            bucket.days.append(day)

        # Punktebene. Ein Querungsbauwerk ist ein Ort wie ein Setzpunkt auch.
        if rec.location_type in (POINT, CROSSING) and rec.location_label:
            pkey = (rec.location_label, rec.area_final, group)
            points_exact[pkey]["t"] += quantity
            points_exact[pkey]["m3"] += volume
            points_exact[pkey]["n"] += 1
            allocations.append({
                "record_id": rec.record_id, "location_point": rec.location_label,
                "sp_number": rec.location_from, "area_final": rec.area_final,
                "material_group": group, "delivery_date": day,
                "allocated_t": round(quantity, 3), "allocated_m3_installed": round(volume, 3),
                "allocation_method": "exact",
            })
        elif rec.location_type == SPAN and rec.location_from is not None and rec.location_to is not None:
            points = [point_label(n) for n in range(rec.location_from, rec.location_to + 1)]
            share_t = quantity / len(points)
            share_m3 = volume / len(points)
            for index, point in enumerate(points):
                pkey = (point, rec.area_final, group)
                points_split[pkey]["t"] += share_t
                points_split[pkey]["m3"] += share_m3
                points_split[pkey]["spans"] += 1
                allocations.append({
                    "record_id": rec.record_id, "location_point": point,
                    "sp_number": rec.location_from + index, "area_final": rec.area_final,
                    "material_group": group, "delivery_date": day,
                    "allocated_t": round(share_t, 3), "allocated_m3_installed": round(share_m3, 3),
                    "allocation_method": "even_split",
                })

    _write(ctx.work_dir / "07_delivery_by_location.csv", BY_LOCATION_COLUMNS, [
        {
            "location_label": label, "location_type": ltype, "location_from": data.location_from,
            "location_to": data.location_to, "span_count": data.span_count,
            "area_final": area, "area_class": data.area_class, "material_group": group,
            "material_key": material, "period_month": month, "deliveries": data.deliveries,
            "delivered_t": round(data.delivered_t, 2),
            "delivered_m3_installed": round(data.delivered_m3_installed, 2),
            "first_delivery": data.first_delivery, "last_delivery": data.last_delivery,
        }
        for (label, ltype, area, group, material, month), data in sorted(by_location.items())
    ])

    point_keys = sorted(set(points_exact) | set(points_split))
    _write(ctx.work_dir / "08_location_points.csv", POINT_COLUMNS, [
        {
            "location_point": point, "sp_number": _sp_number(point), "area_final": area,
            "material_group": group,
            "t_exact": round(points_exact[(point, area, group)]["t"], 2),
            "m3_exact": round(points_exact[(point, area, group)]["m3"], 2),
            "deliveries_exact": int(points_exact[(point, area, group)]["n"]),
            "t_from_spans_even_split": round(points_split[(point, area, group)]["t"], 2),
            "m3_from_spans_even_split": round(points_split[(point, area, group)]["m3"], 2),
            "spans_touching": int(points_split[(point, area, group)]["spans"]),
            "t_total_view": round(points_exact[(point, area, group)]["t"] + points_split[(point, area, group)]["t"], 2),
            "allocation_note": "t_exact ist belegt, t_from_spans_even_split ist eine Gleichverteilung",
        }
        for point, area, group in point_keys
    ])

    _write(ctx.work_dir / "09_location_allocation.csv", ALLOCATION_COLUMNS, sorted(
        allocations, key=lambda a: (str(a["location_point"]), str(a["delivery_date"]), str(a["record_id"]))
    ))

    span_t = by_type_t.get(SPAN, 0.0)
    if span_t:
        ctx.decisions.add(Decision(
            category=6,
            topic="Mengen auf Setzpunktspannen sind nicht punktscharf belegt",
            detail=(
                f"{span_t:,.0f} t sind mit einer Spanne wie 'SP122 - SP131' notiert. Auf welchen Punkt innerhalb der "
                "Spanne wie viel entfiel, steht in keiner Quelle."
            ).replace(",", "."),
            impact=(
                "Auf Punktebene ist dieser Anteil nur ueber eine Gleichverteilung darstellbar. Die Spalte "
                "t_from_spans_even_split haelt ihn deshalb getrennt von der belegten Menge."
            ),
            proposal=(
                "Entweder kuenftig je Fuhre einen Punkt notieren, oder die Gleichverteilung als Naeherung freigeben "
                "und im Bericht so benennen."
            ),
            evidence="work/08_location_points.csv",
        ))

    no_location_t = by_type_t.get("none", 0.0)
    if no_location_t:
        ctx.decisions.add(Decision(
            category=3,
            topic="Lieferungen ohne Ortsangabe",
            detail=(
                f"{no_location_t:,.0f} t von {total_t:,.0f} t ({100 * no_location_t / total_t:.1f} Prozent) tragen keine "
                "verwertbare Ortsangabe im Feld Notes. Ein Teil davon traegt den Hinweis 'no QR specified, only SP'."
            ).replace(",", "."),
            impact="Fuer diesen Anteil ist keine Aussage moeglich, wo das Material eingebaut wurde.",
            proposal="Ortsangabe im Wareneingang verbindlich machen, moeglichst je Fuhre ein Setzpunkt oder Querungsbauwerk.",
            evidence="work/07_delivery_by_location.csv, Zeile 'ohne Ortsangabe'",
        ))

    coverage = 100.0 * located_t / total_t if total_t else 0.0
    ctx.log(
        f"LOCATIONS orte={len(by_location)} punkte={len(point_keys)} abdeckung={coverage:.1f}% "
        f"punktscharf={by_type_t.get(POINT, 0.0):.0f}t spannen={span_t:.0f}t querung={by_type_t.get(CROSSING, 0.0):.0f}t"
    )
    return TaskResult(ok=True, message=f"Ortsabdeckung {coverage:.1f} Prozent", data={
        "locations": len(by_location), "points": len(point_keys), "coverage_pct": round(coverage, 2),
        "t_point": round(by_type_t.get(POINT, 0.0), 2), "t_span": round(span_t, 2),
        "t_crossing": round(by_type_t.get(CROSSING, 0.0), 2), "t_none": round(no_location_t, 2),
    })


def _sp_number(point: str) -> int | None:
    digits = "".join(c for c in point if c.isdigit())
    return int(digits) if digits else None


def _write(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, delimiter=";", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})

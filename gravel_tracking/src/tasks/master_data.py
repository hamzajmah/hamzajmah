"""T0b Stammdaten der Trasse einlesen.

Ergebnis ist `work/01_structures.csv`: je Bauwerksflaeche eine Zeile mit
Sektion, Kilometrierung, Bauweise und Querungsnummer. Daraus entsteht spaeter
die Bereichsdimension mit Ortsbezug.
"""
from __future__ import annotations

import csv

from ..decisions import Decision
from ..harness import Context, TaskResult
from ..master_data import read_structures, section_extent
from ..state import Task

STRUCTURE_COLUMNS = [
    "sequence", "section", "section_key", "structure_name", "area_key",
    "km_from", "km_to", "km_mid", "length_m", "method", "is_crossing",
    "crossing_no", "access_roads",
]


def run(task: Task, ctx: Context) -> TaskResult:
    path = ctx.cfg.path("structure_master")
    if path is None or not path.is_file():
        ctx.decisions.add(Decision(
            category=3,
            topic="Trassenstammdaten fehlen",
            detail="Unter paths.structure_master ist keine lesbare Datei hinterlegt.",
            impact="Querungen lassen sich keiner Sektion zuordnen, eine Kilometrierung fehlt.",
            proposal="Uebersicht der Bauwerksflaechen bereitstellen und Pfad eintragen.",
            evidence="config/config.yaml, paths.structure_master",
        ))
        _write(ctx, [])
        return TaskResult(ok=True, message="keine Stammdaten hinterlegt", data={"structures": 0})

    spec = ctx.cfg.get("structure_master", {})
    structures = read_structures(path, spec.get("sheet", "Termine Beweissich."), int(spec.get("header_row", 4)))
    _write(ctx, structures)

    extent = section_extent(structures)
    crossings = [s for s in structures if s.is_crossing]

    # Gegenprobe: kennt das Stammdatenblatt jede Querung, in die geliefert wurde?
    delivered_areas = {
        r.area_final for r in ctx.store.records()
        if r.area_class == "crossing" and r.charge_type == "material_supply"
    }
    known = {s.area_key for s in crossings}
    unknown = sorted(delivered_areas - known)
    if unknown:
        ctx.decisions.add(Decision(
            category=3,
            topic="Querungen mit Lieferung fehlen im Stammdatenblatt",
            detail=f"In diese Querungen wurde geliefert, sie stehen aber nicht in den Trassenstammdaten: {unknown}",
            impact="Fuer diese Bereiche fehlen Sektionszuordnung, Kilometrierung und Bauweise.",
            proposal="Stammdatenblatt ergaenzen oder klaeren, ob die Buchung falsch ist.",
            evidence="work/01_structures.csv",
        ))

    ctx.log(f"MASTER bauwerke={len(structures)} querungen={len(crossings)} sektionen={len(extent)} unbekannt={len(unknown)}")
    return TaskResult(ok=True, message=f"{len(structures)} Bauwerksflaechen", data={
        "structures": len(structures), "crossings": len(crossings), "sections": len(extent),
    })


def _write(ctx: Context, structures: list) -> None:
    path = ctx.work_dir / "01_structures.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=STRUCTURE_COLUMNS, delimiter=";", lineterminator="\n")
        writer.writeheader()
        for item in structures:
            writer.writerow({
                "sequence": item.sequence, "section": item.section, "section_key": item.section_key,
                "structure_name": item.structure_name, "area_key": item.area_key,
                "km_from": item.km_from, "km_to": item.km_to, "km_mid": item.km_mid,
                "length_m": item.length_m, "method": item.method,
                "is_crossing": "true" if item.is_crossing else "false",
                "crossing_no": item.crossing_no, "access_roads": item.access_roads,
            })

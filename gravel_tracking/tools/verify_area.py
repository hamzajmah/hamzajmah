"""Stichprobe: ein Bereich unabhaengig aus der Quelldatei nachgerechnet.

Bewusst ohne die Pipelinemodule, damit die Nachrechnung nicht dieselben
Annahmen benutzt wie die Auswertung selbst.

    python tools/verify_area.py AS04S-3-04
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "erp" / "P100042563.xlsx"
RECORDS = ROOT / "work" / "02_records.csv"
MATERIAL_PATTERN = r"Mineralgemisch|Sand 0/2"


def main(area: str) -> int:
    raw = pd.read_excel(SOURCE)
    in_area = raw[raw["Sub Project ID"] == area]
    material = in_area[
        in_area["Source Part Description"].str.contains(MATERIAL_PATTERN, na=False)
        & (in_area["Measure Unit"] == "ton")
    ]
    source_rows = len(material)
    source_t = round(float(material["Arrived Source Qty"].sum()), 2)

    records = pd.read_csv(RECORDS, sep=";", low_memory=False)
    pipeline = records[
        (records["area_final"] == area)
        & (records["charge_type"] == "material_supply")
        & (~records["is_duplicate"].astype(bool))
    ]
    pipeline_rows = len(pipeline)
    pipeline_t = round(float(pipeline["quantity_t"].sum()), 2)
    pipeline_m3 = round(float(pipeline["delivered_m3_installed"].sum()), 2)

    print(f"Bereich {area}")
    print(f"  Quelle:   {source_rows} Materialzeilen, {source_t} t")
    print(f"  Pipeline: {pipeline_rows} Saetze, {pipeline_t} t, {pipeline_m3} m3 eingebaut")
    if source_rows == 0:
        print("  Ergebnis: Bereich in der Quelldatei nicht gefunden")
        return 1
    ok = source_rows == pipeline_rows and abs(source_t - pipeline_t) < 0.01
    print("  Ergebnis:", "uebereinstimmend" if ok else "ABWEICHUNG")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "AS04S-3-04"))

"""Vollstaendige Materialzuordnung zum Nachlesen und Freigeben.

Erzeugt outputs/materialzuordnung.md aus dem aktuellen Lauf. Jede Zeile stammt
aus work/02_records.csv bzw. work/05_lv_positions.csv, nichts ist hier
hartkodiert. Damit ist die Zuordnung pruefbar, statt nur behauptet.

    python tools/show_material_mapping.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
RECORDS = ROOT / "work" / "02_records.csv"
LV_POSITIONS = ROOT / "work" / "05_lv_positions.csv"
MAPPING = ROOT / "config" / "lv_mapping.yaml"
FACTORS = ROOT / "config" / "conversion_factors.yaml"
OUTPUT = ROOT / "outputs" / "materialzuordnung.md"

CHARGE_LABELS = {
    "material_supply": ("Lieferung Schotter und Sand", "zaehlt als Liefermenge und wird in Kubikmeter umgerechnet"),
    "disposal_acceptance": ("Annahme beim Lieferanten", "eigene Kennzahl Annahmemenge, nicht Teil der Liefermenge, keine Umrechnung"),
    "surcharge": ("Zuschlag", "Tonnen sind nur Abrechnungsbasis, zaehlen nie"),
    "freight": ("Fracht", "Stunden oder Euro, keine Menge"),
    "unknown": ("nicht klassifiziert", "faellt aus allen Auswertungen"),
}


def _de(value: float, digits: int = 2) -> str:
    return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main() -> int:
    if not RECORDS.exists():
        print("work/02_records.csv fehlt. Zuerst 'python -m src.cli run --until-done' ausfuehren.")
        return 1

    records = pd.read_csv(RECORDS, sep=";", low_memory=False)
    mapping = yaml.safe_load(MAPPING.read_text(encoding="utf-8"))
    factors = yaml.safe_load(FACTORS.read_text(encoding="utf-8"))["factors"]
    material_to_group = mapping.get("material_to_group", {})

    lines = [
        "# Materialzuordnung",
        "",
        "Erzeugt aus dem aktuellen Lauf mit `python tools/show_material_mapping.py`.",
        "Grundlage ist die Spalte `Source Part Description` des IFS Wareneingangs.",
        "Der Originaltext bleibt in jedem Satz erhalten, alle weiteren Spalten sind daraus abgeleitet.",
        "",
        "## 1. Jede Bezeichnung aus dem ERP und was daraus wurde",
        "",
    ]

    grouped = (
        records.groupby(["charge_type", "material_text", "material_class", "grain_size", "rock_type", "unit"], dropna=False)
        .agg(zeilen=("record_id", "count"), menge=("quantity", "sum"), gezaehlt_t=("quantity_t", "sum"))
        .reset_index()
    )
    for charge in ["material_supply", "disposal_acceptance", "surcharge", "freight", "unknown"]:
        part = grouped[grouped["charge_type"] == charge].sort_values("menge", ascending=False)
        if part.empty:
            continue
        label, effect = CHARGE_LABELS[charge]
        lines += [
            f"### {label} (`charge_type = {charge}`)",
            "",
            f"**Wirkung: {effect}.** {len(part)} Bezeichnungen, {_de(part['menge'].sum(), 0)} Einheiten in der Quelle, "
            f"davon als Menge gefuehrt {_de(part['gezaehlt_t'].sum(), 0)} t.",
            "",
            "| Bezeichnung im ERP | Klasse | Koernung | Gestein | Einheit | Zeilen | Menge in der Quelle | als Menge gefuehrt t |",
            "|---|---|---|---|---|---:|---:|---:|",
        ]
        for row in part.itertuples():
            lines.append(
                f"| {row.material_text} | {row.material_class if isinstance(row.material_class, str) else '-'} | "
                f"{row.grain_size if isinstance(row.grain_size, str) else '-'} | "
                f"{row.rock_type if isinstance(row.rock_type, str) else '-'} | {row.unit} | "
                f"{row.zeilen} | {_de(row.menge)} | {_de(row.gezaehlt_t)} |"
            )
        lines.append("")

    lines += [
        "## 2. Umrechnung je Material",
        "",
        "| Material | gelieferte t | lose t/m3 | eingebaut t/m3 | m3 eingebaut | Quelle | Konfidenz |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    supply = records[records["charge_type"] == "material_supply"].copy()
    supply["material_key"] = supply["material_class"].astype(str) + " " + supply["grain_size"].astype(str)
    for key, part in sorted(supply.groupby("material_key")):
        factor_key = key.replace(" ", "_").replace("/", "_")
        entry = factors.get(factor_key, {})
        lines.append(
            f"| {key} | {_de(part['quantity_t'].sum())} | {entry.get('bulk_density_t_per_m3', '-')} | "
            f"{entry.get('installed_density_t_per_m3', '-')} | {_de(part['delivered_m3_installed'].sum())} | "
            f"{entry.get('source', '-')} | {entry.get('confidence', '-')} |"
        )

    lines += [
        "",
        "## 3. Welches Material zaehlt auf welche LV Gruppe",
        "",
        f"Quelle: `config/lv_mapping.yaml`, Status: **{mapping.get('status', 'unbekannt')}**.",
        "",
        "| Material | LV Gruppe | gelieferte t | m3 eingebaut |",
        "|---|---|---:|---:|",
    ]
    for key, part in sorted(supply.groupby("material_key")):
        group = material_to_group.get(key, "nicht zugeordnet")
        lines.append(f"| {key} | {group} | {_de(part['quantity_t'].sum())} | {_de(part['delivered_m3_installed'].sum())} |")

    if LV_POSITIONS.exists():
        lv = pd.read_csv(LV_POSITIONS, sep=";", dtype=str)
        lv = lv[lv["material_group"].notna() & (lv["material_group"] != "")]
        lines += [
            "",
            "## 4. Welche LV Positionen hinter den Gruppen stehen",
            "",
            "| LV Gruppe | Kurztext im LV | Positionen | Vertragsmenge | abgerechnet | im Lieferabgleich |",
            "|---|---|---:|---:|---:|---|",
        ]
        groups_cfg = mapping.get("groups", {})
        agg = lv.groupby(["material_group", "short_text"]).agg(
            n=("lv_position_no", "count"),
            lv_menge=("contract_quantity", lambda s: s.astype(float).sum()),
            re_menge=("billed_quantity", lambda s: s.astype(float).sum()),
        ).reset_index()
        for row in agg.sort_values(["material_group", "lv_menge"], ascending=[True, False]).itertuples():
            relevant = "ja" if groups_cfg.get(row.material_group, {}).get("consumes_delivered_material") else "nein"
            text = row.short_text.replace("\n", " ")
            lines.append(
                f"| {row.material_group} | {text} | {row.n} | {_de(row.lv_menge, 0)} | {_de(row.re_menge, 0)} | {relevant} |"
            )

    lines += [
        "",
        "## 5. Was hier eine Entscheidung braucht",
        "",
        "- Die Zuordnung Material zu LV Gruppe in Abschnitt 3 ist ein Vorschlag, kein Vertragsstand.",
        "- Die Dichten in Abschnitt 2 sind Literaturwerte, kein Projektbeleg.",
        "- Beides steht als offener Punkt in `outputs/DECISIONS.md`.",
        "",
    ]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"geschrieben: {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

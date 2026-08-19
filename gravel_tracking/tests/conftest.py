"""Testfixtures.

Der ERP Fixture enthaelt echte Positionsbezeichnungen und Notizformate aus dem
Wareneingangsexport des Lieferanten, gekuerzt auf wenige Zeilen.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Echte Positionsarten des Lieferanten Baustoff Vertrieb Fulda Werra.
ERP_ROWS = [
    # (part description, qty, unit, date, notes, sub project, activity, receipt no, receipt ref, po line)
    ("Bau Tandemzug + Maut : 310080 Mineralgemisch 0/8 (Kalkstein)", 24.65, "ton", "2026-07-22", "SP37 LS 10044", "AS04S-3-04", "Backfill", 243, "D-2604010417", 1),
    ("gleitenden Diesel- /Energie-zuschlag €0,20/t", 24.65, "ton", "2026-07-22", "SP37 LS 10044", "GENERAL", "Backfill", 1705, "D-2604010417", 2),
    ("gleitenden Diesel- /Energie-zuschlag €0,20/t", 24.65, "ton", "2026-07-22", "SP37 LS 10044", "GENERAL", "Backfill", 1722, "D-2604010417", 4),
    ("Sattel/Tandemzug + Maut : Mineralgemisch 50/200 (Porphyr)", 25.10, "ton", "2026-07-23", "Q127 LS 11627", "QR - 127", "Accesses, Parking Areas And Crossings", 244, "D-2604010418", 6),
    ("Bau Tandemzug + Maut : 0/45 mm Mineralgemisch FSS", 24.00, "ton", "2026-07-24", "SP78 as well", "GENERAL", "Trenching", 245, "D-2604010419", 7),
    ("Bau Tandemzug + Maut : Annahme Erdaushub gem. BM-0 mit BGA", 27.42, "ton", "2026-07-24", "SP78 LS 2643503457", "AS04S-3-01", "Slurry And Soil", 246, "D-2604010420", 8),
    ("SP - Samstagszuschlag", 25.00, "ton", "2026-07-25", "SP47 LS 10171", "GENERAL", "Backfill", 671, "D-2604010448", 10),
    ("SP - Frachtkostenausgleich Schüttgut", 12.50, "€", "2026-07-25", "", "GENERAL", "Backfill", 672, "D-2604010448", 12),
]

ERP_COLUMNS = [
    "Source Ref 1", "Source Ref 2", "Source Ref 3", "Receipt No", "Source Ref Type",
    "Sender Description", "Site", "Source Part Description", "Status", "Arrived Source Qty",
    "Measure Unit", "Actual Delivery Date", "Received By", "Receipt Reference", "Notes",
    "Program Description", "Project ID", "Sub Project ID", "Sub Project Description",
    "Activity ID", "Activity Description",
]


def write_erp_fixture(path: Path) -> None:
    import pandas as pd

    rows = []
    for part, qty, unit, day, notes, sub, activity, receipt_no, receipt_ref, po_line in ERP_ROWS:
        rows.append({
            "Source Ref 1": "P100042563", "Source Ref 2": po_line, "Source Ref 3": 1,
            "Receipt No": receipt_no, "Source Ref Type": "PurchaseOrder",
            "Sender Description": "BAUSTOFF VERTRIEB FULDA WERRA GMBH (LEISTUNGSGEMEINSCHAFT SUEDLINK, LOS 4)",
            "Site": "1048A", "Source Part Description": part, "Status": "Received",
            "Arrived Source Qty": qty, "Measure Unit": unit,
            "Actual Delivery Date": datetime.fromisoformat(day),
            "Received By": "TESTUSER", "Receipt Reference": receipt_ref, "Notes": notes,
            "Program Description": "24 DE TRANSNETBW Suedlink Eschwege", "Project ID": 1,
            "Sub Project ID": sub, "Sub Project Description": sub,
            "Activity ID": "201-70000", "Activity Description": activity,
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=ERP_COLUMNS).to_excel(path, index=False)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Vollstaendiges Miniprojekt in einem temporaeren Verzeichnis."""
    root = tmp_path / "gravel_tracking"
    (root / "config").mkdir(parents=True)
    shutil.copy(PROJECT_ROOT / "config" / "conversion_factors.yaml", root / "config" / "conversion_factors.yaml")
    shutil.copytree(PROJECT_ROOT / "config" / "supplier_templates", root / "config" / "supplier_templates")

    config_text = (PROJECT_ROOT / "config" / "config.yaml").read_text(encoding="utf-8")
    (root / "config" / "config.yaml").write_text(config_text, encoding="utf-8")

    write_erp_fixture(root / "data" / "erp" / "test_receipts.xlsx")
    (root / "data" / "gravel_deliveries" / "_unsorted").mkdir(parents=True)
    return root


@pytest.fixture(autouse=True)
def _clear_config_cache():
    from src.config import load_config, load_conversion_factors

    load_config.cache_clear()
    load_conversion_factors.cache_clear()
    yield
    load_config.cache_clear()
    load_conversion_factors.cache_clear()


def run_cli(root: Path, *args: str) -> int:
    from src.cli import main

    return main(["--config", str(root / "config" / "config.yaml"), *args])

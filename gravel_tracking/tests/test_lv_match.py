"""LV Seite: Leser, Bereichszuordnung und Vergleich."""
from __future__ import annotations

import csv
from pathlib import Path

import yaml

from src.lv_reader import apply_mapping, read_leistungsmeldung
from tests.conftest import run_cli

MAPPING = yaml.safe_load((Path(__file__).resolve().parent.parent / "config" / "lv_mapping.yaml").read_text(encoding="utf-8"))


def _positions(project: Path):
    positions = read_leistungsmeldung(
        project / "data" / "lv" / "leistungsmeldung.xlsx",
        [{"name": "Haupt_Leistung ", "header_row": 13, "lv_source": "Haupt-LV"}],
    )
    apply_mapping(positions, MAPPING)
    return positions


def test_nur_positionszeilen_werden_gelesen(project: Path):
    positions = _positions(project)
    assert [p.lv_position_no for p in positions] == ["2.3.6.30", "4.4.4.20", "4.4.4.10"]


def test_bereich_kommt_aus_dem_gliederungspfad(project: Path):
    by_no = {p.lv_position_no: p for p in _positions(project)}
    assert by_no["4.4.4.20"].area_key == "AS04S-3-04"      # "Abschnitt 04S-3-04"
    assert by_no["2.3.6.30"].area_key == ""                # Baustrasse, erst ueber area_fallback


def test_materialgruppen_zuordnung_trennt_zukauf_von_eigenem_material(project: Path):
    by_no = {p.lv_position_no: p for p in _positions(project)}
    assert by_no["2.3.6.30"].material_group == "gravel_base_layer"
    assert by_no["2.3.6.30"].is_gravel_relevant is True
    assert by_no["4.4.4.20"].material_group == "bedding_supplied"
    assert by_no["4.4.4.20"].is_gravel_relevant is True
    # Aufbereitetes Material stammt aus eigenem Aushub und gehoert nicht in den Lieferabgleich.
    assert by_no["4.4.4.10"].material_group == "bedding_recycled"
    assert by_no["4.4.4.10"].is_gravel_relevant is False


def test_monatsmenge_wird_aus_wochenwert_und_einheitspreis_abgeleitet(project: Path):
    by_no = {p.lv_position_no: p for p in _positions(project)}
    position = by_no["2.3.6.30"]
    assert position.monthly == {"2026-07": 1000.0}         # 59.350 EUR / 59,35 EUR je m3
    assert position.timeline_matches_total is True
    assert position.progress_pct == round(100 * 1000.0 / 42615.0, 2)


def test_vergleich_stellt_lieferung_und_abrechnung_gegenueber(project: Path):
    run_cli(project, "run", "--until-done")
    with (project / "work" / "06_comparison.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))

    bedding = [r for r in rows if r["area_final"] == "AS04S-3-04" and r["material_group"] == "bedding_supplied"]
    assert bedding, rows
    assert any(float(r["billed_m3"] or 0) == 200.0 for r in bedding)

    # Baustrassenposition ohne eigenen Bereich landet ueber area_fallback auf GENERAL.
    general = [r for r in rows if r["area_final"] == "GENERAL" and r["material_group"] == "gravel_base_layer"]
    assert any(float(r["billed_m3"] or 0) == 1000.0 for r in general)


def test_lv_dateien_werden_geschrieben(project: Path):
    run_cli(project, "run", "--until-done")
    positions_file = project / "work" / "05_lv_positions.csv"
    monthly_file = project / "work" / "05_lv_billing_monthly.csv"
    assert positions_file.exists() and monthly_file.exists()
    with monthly_file.open(encoding="utf-8") as fh:
        monthly = list(csv.DictReader(fh, delimiter=";"))
    # Nur schotterrelevante Positionen erscheinen in der Monatsdatei.
    assert {r["lv_position_no"] for r in monthly} == {"2.3.6.30", "4.4.4.20"}


def test_zeitraum_ausserhalb_des_projektzeitraums_wird_markiert(project: Path):
    run_cli(project, "run", "--until-done")
    with (project / "work" / "06_comparison.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    assert all(r["in_comparison_period"] in ("true", "false") for r in rows)
    assert any(r["in_comparison_period"] == "true" for r in rows)

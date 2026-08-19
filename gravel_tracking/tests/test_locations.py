"""Ortsangaben: Parsen, Punktebene und die Trennung belegt gegen verteilt."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.locations import CROSSING, NONE, POINT, SPAN, parse
from tests.conftest import run_cli

# Echte Schreibweisen aus dem Notizfeld des Wareneingangs.
CASES = [
    ("SP37 LS 10044", POINT, "SP037", 37, 37, 1),
    ("SP78 as well", POINT, "SP078", 78, 78, 1),
    ("2SP132 LS 1221", POINT, "SP132", 132, 132, 1),          # Tippfehler im Bestand
    ("SP142a LS 2643504732", POINT, "SP142a", 142, 142, 1),   # Punkt mit Buchstabe
    ("SP122 - SP131", SPAN, "SP122-SP131", 122, 131, 10),
    ("SP162-SP167", SPAN, "SP162-SP167", 162, 167, 6),
    ("SP 48 - SP 51", SPAN, "SP048-SP051", 48, 51, 4),
    ("Q249 LS 2543536083", CROSSING, "QR - 249", 249, 249, 1),
    ("0Q249 LS 809", CROSSING, "QR - 249", 249, 249, 1),
    ("no QR specified, only SP", NONE, "", None, None, 0),
    ("always check with Rafi before booking;no QR specified, only SP", NONE, "", None, None, 0),
    ("", NONE, "", None, None, 0),
]


@pytest.mark.parametrize("note,kind,label,start,end,count", CASES)
def test_ortsangabe_wird_erkannt(note, kind, label, start, end, count):
    place = parse(note)
    assert place.location_type == kind
    assert place.location_label == label
    assert place.location_from == start
    assert place.location_to == end
    assert place.span_count == count


def test_lieferscheinnummer_wird_nicht_als_ort_gelesen():
    """'LS 2543536083' darf nicht als Querung Q2543 durchgehen."""
    assert parse("LS 2543536083").location_type == NONE
    assert parse("SP92 LS 1769").location_label == "SP092"


def test_spanne_liefert_alle_punkte():
    assert parse("SP 48 - SP 51").points == ["SP048", "SP049", "SP050", "SP051"]
    assert parse("SP37").points == ["SP037"]
    assert parse("").points == []


def test_verdrehte_spanne_wird_sortiert():
    place = parse("SP131 - SP122")
    assert (place.location_from, place.location_to) == (122, 131)


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def test_ortsauswertung_trennt_belegte_von_verteilter_menge(project: Path):
    run_cli(project, "run", "--until-done")
    points = _rows(project / "work" / "08_location_points.csv")
    by_point = {r["location_point"]: r for r in points}

    # Fixture: SP37 traegt eine Einzellieferung von 24,65 t.
    assert float(by_point["SP037"]["t_exact"]) == 24.65
    assert float(by_point["SP037"]["t_from_spans_even_split"]) == 0.0


def test_spanne_wird_gleichmaessig_verteilt_und_als_solche_gekennzeichnet(project: Path):
    run_cli(project, "run", "--until-done")
    allocation = _rows(project / "work" / "09_location_allocation.csv")
    methods = {r["allocation_method"] for r in allocation}
    assert methods <= {"exact", "even_split"}
    exact = [r for r in allocation if r["allocation_method"] == "exact"]
    assert exact, "mindestens eine punktscharfe Zuordnung erwartet"
    # Keine Zuordnung darf mehr Menge tragen als die Fuhre selbst.
    assert all(float(r["allocated_t"]) > 0 for r in allocation)


def test_summe_der_zuordnungen_entspricht_der_liefermenge(project: Path):
    """Verteilen darf Menge weder erzeugen noch verlieren."""
    run_cli(project, "run", "--until-done")
    records = _rows(project / "work" / "02_records.csv")
    located = [
        r for r in records
        if r["charge_type"] == "material_supply" and r["is_duplicate"] == "false" and r["location_type"] != "none"
    ]
    expected = round(sum(float(r["quantity_t"]) for r in located), 2)
    allocation = _rows(project / "work" / "09_location_allocation.csv")
    assert round(sum(float(r["allocated_t"]) for r in allocation), 2) == expected


def test_ohne_ortsangabe_erscheint_als_eigene_zeile(project: Path):
    run_cli(project, "run", "--until-done")
    rows = _rows(project / "work" / "07_delivery_by_location.csv")
    labels = {r["location_label"] for r in rows}
    assert "ohne Ortsangabe" in labels

"""Laufzeitverhalten der Pipeline: Idempotenz, Resume, Mengenlogik."""
from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import run_cli


def _records(root: Path) -> str:
    return (root / "work" / "02_records.csv").read_text(encoding="utf-8")


def _quality(root: Path) -> dict:
    return json.loads((root / "work" / "quality.json").read_text(encoding="utf-8"))


def test_lauf_bis_warteschlange_leer(project: Path):
    assert run_cli(project, "run", "--until-done") == 0
    state = json.loads((project / "work" / "state.json").read_text(encoding="utf-8"))
    assert all(t["status"] == "done" for t in state["tasks"].values()), state["tasks"]
    assert (project / "outputs" / "run_report.md").exists()
    assert (project / "outputs" / "DECISIONS.md").exists()
    assert (project / "outputs" / "powerbi" / "gravel_model.xlsx").exists()
    assert (project / "METHOD.md").exists()


def test_zweiter_lauf_erzeugt_keine_duplikate(project: Path):
    """Idempotenz: derselbe Bestand, zweimal verarbeitet, ergibt dieselbe Datei."""
    run_cli(project, "run", "--until-done")
    first = _records(project)
    first_quality = _quality(project)

    run_cli(project, "run", "--until-done")
    assert _records(project) == first
    assert _quality(project) == first_quality


def test_resume_setzt_exakt_fort(project: Path, tmp_path: Path):
    """Ein Lauf wird nach zwei Tasks abgebrochen und fortgesetzt.

    Das Ergebnis muss identisch zu einem ununterbrochenen Lauf sein.
    """
    assert run_cli(project, "run", "--max-tasks", "2") == 0
    state = json.loads((project / "work" / "state.json").read_text(encoding="utf-8"))
    assert any(t["status"] == "pending" for t in state["tasks"].values())
    assert not (project / "outputs" / "run_report.md").exists()

    assert run_cli(project, "resume") == 0
    state = json.loads((project / "work" / "state.json").read_text(encoding="utf-8"))
    assert all(t["status"] == "done" for t in state["tasks"].values())
    resumed = _records(project)

    # Vergleichslauf ohne Unterbrechung in einem frischen Projekt.
    import shutil

    reference = tmp_path / "reference"
    shutil.copytree(project, reference, ignore=shutil.ignore_patterns("work", "outputs", "METHOD.md"))
    assert run_cli(reference, "run", "--until-done") == 0
    assert _records(reference) == resumed


def test_zuschlaege_zaehlen_nicht_als_liefermenge(project: Path):
    run_cli(project, "run", "--until-done")
    import csv

    with (project / "work" / "02_records.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))

    supply = [r for r in rows if r["charge_type"] == "material_supply"]
    surcharge = [r for r in rows if r["charge_type"] in ("surcharge", "freight")]
    assert surcharge, "Fixture enthaelt Zuschlagszeilen"
    # Zuschlagszeilen tragen Tonnen als Abrechnungsbasis, aber nie quantity_t.
    assert all(r["quantity_t"] == "" for r in surcharge)
    assert all(r["delivered_m3_installed"] == "" for r in surcharge)
    assert sum(float(r["quantity_t"]) for r in supply) == 115.75  # 24,65 + 25,10 + 24,00 + 20,00 + 22,00


def test_annahme_wird_von_der_lieferung_getrennt(project: Path):
    run_cli(project, "run", "--until-done")
    import csv

    with (project / "work" / "02_records.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    disposal = [r for r in rows if r["charge_type"] == "disposal_acceptance"]
    assert len(disposal) == 1
    assert disposal[0]["quantity_t"] == "27.42"
    assert disposal[0]["delivered_m3_installed"] == ""  # keine Schotterumrechnung


def test_doppelte_zuschlagszeile_wird_als_dublette_erkannt(project: Path):
    run_cli(project, "run", "--until-done")
    import csv

    with (project / "work" / "03_duplicates.csv").open(encoding="utf-8") as fh:
        duplicates = list(csv.DictReader(fh, delimiter=";"))
    assert len(duplicates) == 1
    assert duplicates[0]["delivery_note_no"] == "10044"
    assert _quality(project)["duplicates_removed"] == 1


def test_bereich_kommt_aus_der_buchung_und_wird_nicht_ueberschrieben(project: Path):
    run_cli(project, "run", "--until-done")
    import csv

    with (project / "work" / "02_records.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    by_note = {r["source_row_ref"]: r for r in rows}
    material = next(r for r in rows if "310080 Mineralgemisch" in r["material_text"])
    assert material["area_final"] == "AS04S-3-04"
    assert material["area_class"] == "section"
    assert material["area_from_document"] == ""
    assert material["area_conflict"] == "false"
    assert by_note

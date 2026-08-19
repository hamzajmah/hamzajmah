"""Vorlagenparsing gegen Rohtextfixtures des Lieferanten."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from src.parsing import detect_supplier, normalize_number, parse_date_de, parse_document

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TEMPLATE = yaml.safe_load(
    (Path(__file__).resolve().parent.parent / "config" / "supplier_templates" / "baustoff_vertrieb_fulda_werra.yaml").read_text(encoding="utf-8")
)

EXPECTED = {
    "lieferschein_01.txt": ("2640801638", date(2026, 6, 3), 24.65, None),
    "lieferschein_02.txt": ("2643503457", date(2026, 7, 24), 25.10, 16.19),
    "lieferschein_03.txt": ("11627", date(2026, 7, 7), 13.68, None),
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_vorlage_liest_pflichtfelder(name):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    assert detect_supplier(text, [TEMPLATE]) is TEMPLATE

    notes = parse_document(text, TEMPLATE)
    assert len(notes) == 1
    note = notes[0]
    note_no, day, quantity, m3 = EXPECTED[name]
    assert note.delivery_note_no == note_no
    assert note.delivery_date == day
    assert note.quantity_t == quantity
    assert note.quantity_m3_doc == m3
    assert note.confidence >= 0.80


def test_unbekannter_lieferant_wird_nicht_geraten():
    assert detect_supplier("Irgendein anderer Lieferant\nMenge 20,0 t", [TEMPLATE]) is None


def test_fehlende_felder_bleiben_leer():
    notes = parse_document("BAUSTOFF VERTRIEB FULDA WERRA GMBH\nkeine weiteren Angaben", TEMPLATE)
    assert notes[0].delivery_note_no == ""
    assert notes[0].quantity_t is None
    assert notes[0].confidence < 0.80


@pytest.mark.parametrize("raw,expected", [("24,65", 24.65), ("1.234,56", 1234.56), ("1234.56", 1234.56), ("", None), ("abc", None)])
def test_zahlformate(raw, expected):
    assert normalize_number(raw) == expected


@pytest.mark.parametrize("raw,expected", [("03.06.2026", date(2026, 6, 3)), ("3.6.26", date(2026, 6, 3)), ("32.13.2026", None)])
def test_datumsformate(raw, expected):
    assert parse_date_de(raw) == expected

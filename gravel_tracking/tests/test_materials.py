"""Klassifikation echter Positionsbezeichnungen des Lieferanten."""
from __future__ import annotations

import pytest

from src.materials import classify, factor_key

CASES = [
    ("Bau Tandemzug + Maut : 310080 Mineralgemisch 0/8 (Kalkstein)", "material_supply", "mineral_mixture", "0/8", "mineral_mixture_0_8"),
    ("Sattel/Tandemzug + Maut : Mineralgemisch 50/200 (Porphyr)", "material_supply", "mineral_mixture", "50/200", "mineral_mixture_50_200"),
    ("Bau Tandemzug + Maut : 0/45 mm Mineralgemisch FSS", "material_supply", "mineral_mixture", "0/45", "mineral_mixture_0_45"),
    ("Sattel/Tandemzug + Maut: Mineralgemisch 0/22 (Kalkstein)", "material_supply", "mineral_mixture", "0/22", "mineral_mixture_0_22"),
    ("Bau Tandemzug + Maut : Sand 0/2 – Rundkorn (Kabelsand 0/1)", "material_supply", "sand", "0/2", "sand_0_2"),
    ("Bau Tandemzug + Maut : Annahme Erdaushub gem. BM-0 mit BGA", "disposal_acceptance", "excavated_soil", "", ""),
    ("Bau 3-Achser + Maut : Annahme Bohrklein (mit Analyse)", "disposal_acceptance", "drill_cuttings", "", ""),
    ("gleitenden Diesel- /Energie-zuschlag €0,40/t", "surcharge", "", "", ""),
    ("SP - Samstagszuschlag", "surcharge", "", "", ""),
    ("SP - Frachtkostenausgleich Schüttgut", "freight", "", "", ""),
]


@pytest.mark.parametrize("text,charge,material_class,grain,key", CASES)
def test_classification(text, charge, material_class, grain, key):
    info = classify(text)
    assert info.charge_type == charge
    assert info.material_class == material_class
    assert info.grain_size == grain
    assert factor_key(info) == key
    # Der Originaltext bleibt unveraendert erhalten.
    assert info.material_text == text


# Schreibweisen aus dem Lieferlog des Lieferanten. Gemeint ist dasselbe
# Material wie im Wareneingang, nur anders benannt.
LOG_CASES = [
    ("Baustoffgemisch 0/45 für FSS", "material_supply", "mineral_mixture", "0/45"),
    ("Bausoffgemisch 0/45 für FSS", "material_supply", "mineral_mixture", "0/45"),
    ("Korngemisch 0/8 (Ladergemisch)", "material_supply", "mineral_mixture", "0/8"),
    ("FSS 0/45 Natursand", "material_supply", "mineral_mixture", "0/45"),
    ("Frostschutz 0/32", "material_supply", "mineral_mixture", "0/32"),
    ("Brechsand 0/2 (feine Gesteinskörnung)", "material_supply", "sand", "0/2"),
    ("Edelsplitt 2/5 grobe Gk", "material_supply", "mineral_mixture", "2/5"),
    ("Erdaushub gem. BM-0 mit BGA", "disposal_acceptance", "excavated_soil", ""),
]


@pytest.mark.parametrize("text,charge,material_class,grain", LOG_CASES)
def test_lieferlog_bezeichnungen(text, charge, material_class, grain):
    info = classify(text)
    assert info.charge_type == charge
    assert info.material_class == material_class
    assert info.grain_size == grain


def test_natursand_ist_tragschicht_kein_bettungssand():
    """'FSS 0/45 Natursand' ist Frostschutzmaterial, kein Kabelsand."""
    assert classify("FSS 0/45 Natursand").material_class == "mineral_mixture"
    assert classify("Sand 0/2 (feine Geseinskornung)").material_class == "sand"


def test_zuschlag_wird_nie_als_lieferung_gewertet():
    """Der teuerste denkbare Fehler: Zuschlagszeilen als Liefermenge zaehlen."""
    for text in ("gleitenden Diesel- /Energie-zuschlag €1,00/t", "QR - Samstagszuschlag"):
        assert classify(text).charge_type != "material_supply"

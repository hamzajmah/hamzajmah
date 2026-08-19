from __future__ import annotations

from pathlib import Path

from src.config import load_conversion_factors
from src.conversion import convert

FACTORS = load_conversion_factors(str(Path(__file__).resolve().parent.parent / "config" / "conversion_factors.yaml"))


def test_lose_und_eingebaut_werden_getrennt_ausgewiesen():
    result = convert(24.0, "mineral_mixture_0_45", FACTORS)
    assert result.m3_loose == 15.0          # 24 t / 1,60 t je m3
    assert result.m3_installed == 12.0      # 24 t / 2,00 t je m3
    # Wer mit der losen Dichte gegen das LV vergleicht, erzeugt ein Scheindelta.
    assert result.m3_loose > result.m3_installed


def test_sensitivitaet_umschliesst_den_mittelwert():
    result = convert(100.0, "mineral_mixture_0_8", FACTORS)
    assert result.m3_installed_low < result.m3_installed < result.m3_installed_high


def test_ohne_faktor_keine_umrechnung():
    assert convert(24.0, "", FACTORS).m3_installed is None
    assert convert(24.0, "unbekannte_klasse", FACTORS).m3_installed is None
    assert convert(None, "mineral_mixture_0_8", FACTORS).m3_installed is None


def test_jeder_faktor_hat_eine_quelle():
    for key, entry in FACTORS["factors"].items():
        assert entry.get("source"), f"{key} ohne Quelle"
        assert entry.get("confidence") in ("measured", "assumption")

"""Einheitenumrechnung Tonnen -> Kubikmeter.

Faktoren kommen ausschliesslich aus config/conversion_factors.yaml.
Es werden immer beide Werte ausgegeben: lose und eingebaut. Der Vergleich zum
LV erfolgt ausschliesslich gegen delivered_m3_installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Conversion:
    m3_loose: float | None
    m3_installed: float | None
    m3_installed_low: float | None
    m3_installed_high: float | None
    source: str
    confidence: str
    factor_key: str


EMPTY = Conversion(None, None, None, None, "", "none", "")


def convert(quantity_t: float | None, factor_key: str, factors: dict[str, Any]) -> Conversion:
    if quantity_t is None or not factor_key:
        return EMPTY
    entry = (factors.get("factors") or {}).get(factor_key)
    if not entry:
        return EMPTY
    bulk = float(entry["bulk_density_t_per_m3"])
    installed = float(entry["installed_density_t_per_m3"])
    sens = float((factors.get("defaults") or {}).get("sensitivity_pct", 10.0)) / 100.0
    # Hoehere Dichte -> weniger m3. Untere Grenze der Menge entsteht aus der
    # hoeheren Dichte, obere Grenze aus der niedrigeren.
    return Conversion(
        m3_loose=round(quantity_t / bulk, 3),
        m3_installed=round(quantity_t / installed, 3),
        m3_installed_low=round(quantity_t / (installed * (1 + sens)), 3),
        m3_installed_high=round(quantity_t / (installed * (1 - sens)), 3),
        source=str(entry.get("source", "")),
        confidence=str(entry.get("confidence", "assumption")),
        factor_key=factor_key,
    )

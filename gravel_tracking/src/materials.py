"""Materialklassifikation aus dem unveraenderten Originaltext.

`material_text` bleibt immer im Original erhalten. Diese Funktionen leiten
ausschliesslich zusaetzliche Spalten ab und raten nie: was nicht eindeutig
erkennbar ist, bleibt leer und setzt needs_review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# IFS-Teilebezeichnungen tragen ein Transportpraefix vor dem Doppelpunkt,
# z.B. "Bau Tandemzug + Maut : 310080 Mineralgemisch 0/8 (Kalkstein)".
_PREFIX_SPLIT = re.compile(r"^(?P<prefix>[^:]{0,60}?(?:Maut|Achser|Tandem|Sattel)[^:]{0,20})\s*:\s*(?P<rest>.+)$")
_GRAIN = re.compile(r"\b(\d{1,3})\s*/\s*(\d{1,3})\b")
_ROCK = {
    "kalkstein": "Kalkstein",
    "porphyr": "Porphyr",
    "hartgestein": "Hartgestein",
    "rundkorn": "Rundkorn",
}

# Dasselbe Material, drei Schreibweisen: der IFS Wareneingang nennt es
# Mineralgemisch, das Lieferlog des Lieferanten Baustoffgemisch, Korngemisch
# oder Frostschutz.
_MIXTURE_TOKENS = (
    "mineralgemisch", "baustoffgemisch", "bausoffgemisch", "korngemisch",
    "schotter", "splitt", "frostschutz", "frostchutz",
)
_MIXTURE_SAND_TOKENS = ("natursand", "fss", "frostschutz", "frostchutz")

CHARGE_SUPPLY = "material_supply"
CHARGE_DISPOSAL = "disposal_acceptance"
CHARGE_SURCHARGE = "surcharge"
CHARGE_FREIGHT = "freight"
CHARGE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class MaterialInfo:
    material_text: str          # Originaltext, unveraendert
    material_core: str          # Text ohne Transportpraefix
    charge_type: str
    material_class: str
    grain_size: str
    rock_type: str
    transport_class: str


def _transport_class(prefix: str) -> str:
    p = prefix.lower()
    if "sattel" in p and "tandem" in p:
        return "Sattel/Tandemzug"
    if "tandem" in p:
        return "Tandemzug"
    if "4-achser" in p:
        return "4-Achser"
    if "3-achser" in p:
        return "3-Achser"
    return ""


def classify(material_text: str) -> MaterialInfo:
    text = (material_text or "").strip()
    prefix, core = "", text
    m = _PREFIX_SPLIT.match(text)
    if m:
        prefix, core = m.group("prefix").strip(), m.group("rest").strip()

    low = core.lower()
    charge = CHARGE_UNKNOWN
    material_class = ""
    grain = ""
    rock = ""

    if "zuschlag" in low:
        charge = CHARGE_SURCHARGE
    elif "frachtkosten" in low:
        charge = CHARGE_FREIGHT
    elif low.startswith("annahme") or "annahme " in low or "erdaushub" in low or "bohrklein" in low:
        # Der IFS Wareneingang schreibt "Annahme Erdaushub ...", das Lieferlog
        # nur "Erdaushub ...". Beides ist Entsorgung, keine Lieferung.
        charge = CHARGE_DISPOSAL
        if "erdaushub" in low:
            material_class = "excavated_soil"
        elif "bohrklein" in low:
            material_class = "drill_cuttings"
    elif any(token in low for token in _MIXTURE_TOKENS):
        charge = CHARGE_SUPPLY
        material_class = "mineral_mixture"
    elif "sand" in low or "kabelsand" in low:
        charge = CHARGE_SUPPLY
        # "FSS 0/45 Natursand" ist Frostschutzmaterial, kein Bettungssand.
        # Der Zusatz entscheidet, nicht das Wort Sand.
        material_class = "mineral_mixture" if any(t in low for t in _MIXTURE_SAND_TOKENS) else "sand"

    if charge == CHARGE_SUPPLY:
        g = _GRAIN.search(core)
        if g:
            grain = f"{int(g.group(1))}/{int(g.group(2))}"
        for token, label in _ROCK.items():
            if token in low:
                rock = label
                break

    return MaterialInfo(
        material_text=text,
        material_core=core,
        charge_type=charge,
        material_class=material_class,
        grain_size=grain,
        rock_type=rock,
        transport_class=_transport_class(prefix),
    )


def factor_key(info: MaterialInfo) -> str:
    """Schluessel in conversion_factors.yaml, leer wenn nicht bestimmbar."""
    if info.charge_type != CHARGE_SUPPLY or not info.material_class or not info.grain_size:
        return ""
    return f"{info.material_class}_{info.grain_size.replace('/', '_')}"

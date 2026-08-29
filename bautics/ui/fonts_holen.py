"""Schriften einmalig herunterladen und lokal ablegen.

Grund: Auf der Baustelle ist das Netz schlecht, und ein Aufruf an einen
fremden CDN (Google Fonts) ist bei deutschen Konzernkunden ein unnoetiger
Datenschutz-Diskussionspunkt. Deshalb liegen die Schriftdateien im Repo und
werden vom eigenen Server ausgeliefert.

Aufruf (nur noetig, wenn Schriften erneuert werden sollen):

    python ui/fonts_holen.py

Erzeugt ``app/static/fonts/*.woff2`` und ``app/static/fonts/schriften.css``.
Die erzeugte CSS-Datei wird von ``ui/input.css`` eingebunden.
"""

import re
import sys
import urllib.request
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
ZIEL = WURZEL / "app" / "static" / "fonts"

# Chrome-Kennung, damit Google woff2 statt aelterer Formate ausliefert.
BROWSER = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Genau die Schnitte aus DESIGN.md - nicht mehr.
QUELLE = (
    "https://fonts.googleapis.com/css2"
    "?family=Fraunces:opsz,wght@9..144,500;9..144,560"
    "&family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400"
    "&family=IBM+Plex+Mono:wght@400;500"
    "&display=swap"
)

# Nur westeuropaeische Zeichensaetze - Vietnamesisch, Kyrillisch und
# Griechisch braucht diese Oberflaeche nicht und kostet nur Bytes.
ERLAUBTE_SUBSETS = {"latin", "latin-ext"}


def _lade(url: str) -> bytes:
    anfrage = urllib.request.Request(url, headers={"User-Agent": BROWSER})
    with urllib.request.urlopen(anfrage, timeout=60) as antwort:  # noqa: S310
        return antwort.read()


def _dateiname(familie: str, gewicht: str, stil: str, subset: str) -> str:
    kurz = familie.lower().replace(" ", "-")
    kursiv = "-italic" if stil == "italic" else ""
    return f"{kurz}-{gewicht}{kursiv}-{subset}.woff2"


def main() -> int:
    ZIEL.mkdir(parents=True, exist_ok=True)
    css = _lade(QUELLE).decode("utf-8")

    ausgabe: list[str] = [
        "/* Erzeugt von ui/fonts_holen.py - nicht von Hand aendern. */",
        "/* Schriften aus DESIGN.md, lokal ausgeliefert (kein CDN-Aufruf). */",
        "",
    ]
    geladen = 0
    subset = ""
    for block in re.split(r"(?=/\* [a-z-]+ \*/)", css):
        kopf = re.match(r"/\* ([a-z-]+) \*/", block.strip())
        if kopf:
            subset = kopf.group(1)
        if subset not in ERLAUBTE_SUBSETS or "@font-face" not in block:
            continue
        familie = re.search(r"font-family: '([^']+)'", block)
        gewicht = re.search(r"font-weight: (\d+)", block)
        stil = re.search(r"font-style: (\w+)", block)
        url = re.search(r"url\((https://[^)]+\.woff2)\)", block)
        bereich = re.search(r"unicode-range: ([^;]+);", block)
        if not (familie and gewicht and stil and url):
            continue

        name = _dateiname(familie.group(1), gewicht.group(1), stil.group(1), subset)
        (ZIEL / name).write_bytes(_lade(url.group(1)))
        geladen += 1
        print(f"  {name} ({(ZIEL / name).stat().st_size // 1024} kB)")

        zeilen = [
            "@font-face {",
            f"  font-family: '{familie.group(1)}';",
            f"  font-style: {stil.group(1)};",
            f"  font-weight: {gewicht.group(1)};",
            "  font-display: swap;",
            # Absoluter Pfad unter dem /static-Mount: Das kompilierte
            # Stylesheet liegt in einem anderen Ordner als die Schriften,
            # ein relativer Pfad wuerde dort ins Leere zeigen.
            f"  src: url('/static/fonts/{name}') format('woff2');",
        ]
        if bereich:
            zeilen.append(f"  unicode-range: {bereich.group(1).strip()};")
        zeilen.append("}")
        ausgabe.append("\n".join(zeilen))

    (ZIEL / "schriften.css").write_text("\n".join(ausgabe) + "\n", encoding="utf-8")
    print(f"{geladen} Schriftdateien in {ZIEL} abgelegt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

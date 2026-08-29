"""Mind, Schritt 1: Dateien aus der Wissensbank einlesen.

Dieses Modul kennt nur Dateien und Text - keine Datenbank, kein Modell. Es
liefert je Dokument die Seiten mit ihrem Text; die Zerlegung in Chunks macht
``chunking.py``.

Aufbau des Wissensbank-Ordners (``BAUTICS_KNOWLEDGE_DIR``)::

    wissensbank/
      SuedLink Baulos 4/      <- Unterordner = Baulos/Projekt
        LV_Los4.pdf
        Bauvertrag.docx
      Allgemein.pdf           <- direkt im Wurzelordner = Standardprojekt

TODO: Gescannte PDFs ohne Textebene brauchen OCR. Bis dahin werden sie
erkannt und gemeldet (``KeinTextFehler``) - lieber eine sichtbare Luecke als
ein leer indexiertes Dokument, das Mind spaeter nicht zitieren kann.
"""

import datetime as dt
import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from . import config

logger = logging.getLogger(__name__)

# Dateiendung -> Kuerzel des Formats
UNTERSTUETZTE_FORMATE = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
}

# Formate mit echten Seitenzahlen - dort ist die Seite in der Fundstelle Pflicht.
FORMATE_MIT_SEITEN = frozenset({"pdf"})

# Dokumenttyp aus dem Dateinamen. Grob, aber gut genug zum Filtern und
# fuer die Anzeige der Fundstelle. Reihenfolge = Vorrang.
_TYP_MUSTER: tuple[tuple[str, str], ...] = (
    ("nachtrag", "Nachtrag"),
    ("leistungsverzeichnis", "Leistungsverzeichnis"),
    ("lv_", "Leistungsverzeichnis"),
    ("gaeb", "Leistungsverzeichnis"),
    ("protokoll", "Protokoll"),
    ("besprechung", "Protokoll"),
    ("bautagebuch", "Bautagesbericht"),
    ("tagesbericht", "Bautagesbericht"),
    ("vertrag", "Vertrag"),
    ("vob", "Vertrag"),
    ("plan", "Plan"),
    ("gutachten", "Gutachten"),
    ("baugrund", "Gutachten"),
    ("schriftverkehr", "Schriftverkehr"),
)

DOKUMENTTYP_STANDARD = "Dokument"


class DokumentFehler(RuntimeError):
    """Die Datei konnte nicht eingelesen werden."""


class KeinTextFehler(DokumentFehler):
    """Die Datei enthaelt keinen extrahierbaren Text (vermutlich ein Scan)."""


@dataclass(frozen=True)
class Seite:
    """Eine Seite bzw. ein Textblock eines Dokuments.

    ``nummer`` ist bei PDF die echte Seitenzahl (1-basiert). Formate ohne
    Seitenbegriff (DOCX, TXT, Markdown) liefern ``None``; dort traegt der
    Abschnitt die Fundstelle.
    """

    nummer: Optional[int]
    text: str


@dataclass(frozen=True)
class DokumentInhalt:
    """Ein eingelesenes Dokument samt Provenienz-Angaben."""

    pfad: Path
    dateiname: str
    dateiformat: str
    dokumenttyp: str
    projekt: str
    geaendert_am: dt.datetime
    datei_hash: str
    seiten: list[Seite]

    @property
    def seiten_pflicht(self) -> bool:
        return self.dateiformat in FORMATE_MIT_SEITEN


def berechne_hash(pfad: Path) -> str:
    """SHA-256 der Datei - erkennt, ob sich der Inhalt geaendert hat."""
    streuwert = hashlib.sha256()
    with pfad.open("rb") as datei:
        for block in iter(lambda: datei.read(1024 * 1024), b""):
            streuwert.update(block)
    return streuwert.hexdigest()


def finde_dokumente(verzeichnis: Optional[Path] = None) -> list[Path]:
    """Alle unterstuetzten Dateien im Wissensbank-Ordner, sortiert."""
    wurzel = verzeichnis or config.KNOWLEDGE_DIR
    if not wurzel.exists():
        logger.warning("Wissensbank-Ordner %s existiert nicht.", wurzel)
        return []
    treffer = [
        pfad
        for pfad in sorted(wurzel.rglob("*"))
        if pfad.is_file()
        and pfad.suffix.lower() in UNTERSTUETZTE_FORMATE
        and not pfad.name.startswith((".", "~$"))  # Systemdateien, Office-Sperrdateien
    ]
    return treffer


def projekt_aus_pfad(pfad: Path, wurzel: Optional[Path] = None) -> str:
    """Erster Unterordner unterhalb der Wurzel = Baulos/Projekt."""
    basis = wurzel or config.KNOWLEDGE_DIR
    try:
        relativ = pfad.resolve().relative_to(basis.resolve())
    except ValueError:
        return config.PROJEKT_STANDARD
    return relativ.parts[0] if len(relativ.parts) > 1 else config.PROJEKT_STANDARD


def dokumenttyp_aus_name(dateiname: str) -> str:
    kleingeschrieben = dateiname.lower()
    for muster, typ in _TYP_MUSTER:
        if muster in kleingeschrieben:
            return typ
    return DOKUMENTTYP_STANDARD


def lese_dokument(pfad: Path, wurzel: Optional[Path] = None) -> DokumentInhalt:
    """Datei einlesen und als ``DokumentInhalt`` zurueckgeben.

    Wirft ``KeinTextFehler``, wenn keine einzige Seite Text enthaelt - genau
    der Fall des gescannten PDFs, das ohne OCR nichts beitragen kann.
    """
    if not pfad.is_file():
        raise DokumentFehler(f"Datei nicht gefunden: {pfad.name}")

    dateiformat = UNTERSTUETZTE_FORMATE.get(pfad.suffix.lower())
    if dateiformat is None:
        raise DokumentFehler(f"Format wird nicht unterstuetzt: {pfad.suffix}")

    if dateiformat == "pdf":
        seiten = list(_lies_pdf(pfad))
    elif dateiformat == "docx":
        seiten = list(_lies_docx(pfad))
    else:
        seiten = list(_lies_text(pfad))

    seiten = [seite for seite in seiten if seite.text.strip()]
    if not seiten:
        raise KeinTextFehler(
            f"{pfad.name}: kein extrahierbarer Text gefunden "
            "(vermutlich ein Scan - OCR ist noch nicht umgesetzt)."
        )

    statistik = pfad.stat()
    return DokumentInhalt(
        pfad=pfad,
        dateiname=pfad.name,
        dateiformat=dateiformat,
        dokumenttyp=dokumenttyp_aus_name(pfad.name),
        projekt=projekt_aus_pfad(pfad, wurzel),
        geaendert_am=dt.datetime.fromtimestamp(statistik.st_mtime, dt.timezone.utc),
        datei_hash=berechne_hash(pfad),
        seiten=seiten,
    )


# --- Formate ---------------------------------------------------------------


def _lies_pdf(pfad: Path) -> Iterator[Seite]:
    try:
        from pypdf import PdfReader
    except ImportError as fehler:  # pragma: no cover - Abhaengigkeit fehlt
        raise DokumentFehler("pypdf ist nicht installiert.") from fehler

    try:
        leser = PdfReader(str(pfad))
    except Exception as fehler:  # noqa: BLE001 - kaputte PDFs sind Alltag
        raise DokumentFehler(f"{pfad.name}: PDF nicht lesbar ({type(fehler).__name__}).") from fehler

    leere_seiten = 0
    for nummer, seite in enumerate(leser.pages, start=1):
        try:
            roh = seite.extract_text() or ""
        except Exception:  # noqa: BLE001 - eine kaputte Seite kippt nicht das Dokument
            logger.warning("%s: Seite %s nicht lesbar.", pfad.name, nummer)
            roh = ""
        text = saeubere_text(roh)
        if not text:
            leere_seiten += 1
        yield Seite(nummer=nummer, text=text)

    if leere_seiten:
        # Nur zaehlen, nicht inhaltlich loggen (Kundendaten).
        logger.info("%s: %s Seite(n) ohne Textebene uebersprungen.", pfad.name, leere_seiten)


def _lies_docx(pfad: Path) -> Iterator[Seite]:
    """DOCX in einen Textblock ueberfuehren - Absaetze und Tabellen in Reihenfolge.

    DOCX kennt keine verlaessliche Seitenzahl (die entsteht erst beim Rendern),
    deshalb gibt es genau einen Block ohne Seitennummer. Die Fundstelle traegt
    dann die Ueberschrift des Abschnitts.
    """
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as fehler:  # pragma: no cover - Abhaengigkeit fehlt
        raise DokumentFehler("python-docx ist nicht installiert.") from fehler

    try:
        dokument = docx.Document(str(pfad))
    except Exception as fehler:  # noqa: BLE001
        raise DokumentFehler(
            f"{pfad.name}: DOCX nicht lesbar ({type(fehler).__name__})."
        ) from fehler

    teile: list[str] = []
    for element in dokument.element.body:
        if element.tag.endswith("}p"):
            absatz = Paragraph(element, dokument)
            text = absatz.text.strip()
            if not text:
                continue
            if _ist_docx_ueberschrift(absatz):
                # Als eigene Zeile mit Leerzeilen davor/danach: so erkennt der
                # Chunker die Abschnittsgrenze wieder.
                teile.append(f"\n{text}\n")
            else:
                teile.append(text)
        elif element.tag.endswith("}tbl"):
            tabelle = Table(element, dokument)
            for zeile in tabelle.rows:
                zellen = [zelle.text.strip().replace("\n", " ") for zelle in zeile.cells]
                zeilentext = " | ".join(zelle for zelle in zellen if zelle)
                if zeilentext:
                    teile.append(zeilentext)

    yield Seite(nummer=None, text=saeubere_text("\n\n".join(teile)))


def _ist_docx_ueberschrift(absatz) -> bool:
    name = (getattr(absatz.style, "name", "") or "").lower()
    return name.startswith(("heading", "überschrift", "ueberschrift", "title", "titel"))


def _lies_text(pfad: Path) -> Iterator[Seite]:
    roh = pfad.read_text(encoding="utf-8", errors="replace")
    yield Seite(nummer=None, text=saeubere_text(roh))


# --- Textbereinigung -------------------------------------------------------

_TRENNUNG_AM_ZEILENENDE = re.compile(r"(\w)[-­]\n(\w)")
_MEHRFACHE_LEERZEILEN = re.compile(r"\n{3,}")
_MEHRFACHE_LEERZEICHEN = re.compile(r"[ \t ]{2,}")


def saeubere_text(roh: str) -> str:
    """PDF-Rohtext in etwas verwandeln, das man zitieren kann.

    Wichtig fuer die Zitatpruefung: Was hier herauskommt, ist der Text, den das
    Modell zu sehen bekommt und woertlich zitieren muss. Deshalb wird genau
    einmal - hier - normalisiert und nicht spaeter noch einmal anders.
    """
    if not roh:
        return ""
    text = unicodedata.normalize("NFKC", roh)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Am Zeilenende getrennte Woerter wieder zusammensetzen ("Kabel-\nschutzrohr").
    text = _TRENNUNG_AM_ZEILENENDE.sub(r"\1\2", text)
    text = text.replace("­", "")  # weiches Trennzeichen
    zeilen = [_MEHRFACHE_LEERZEICHEN.sub(" ", zeile).strip() for zeile in text.split("\n")]
    text = "\n".join(zeilen)
    text = _MEHRFACHE_LEERZEILEN.sub("\n\n", text)
    return text.strip()

"""Mind, Schritt 2: Dokument-Seiten in zitierfaehige Chunks zerlegen.

Zwei Grundsaetze:

1. **Geschnitten wird an natuerlichen Grenzen.** Ein Abschnitt bleibt
   zusammen, ein Absatz wird nicht mitten im Satz zerteilt. Die Zeichenzahl
   ist eine Obergrenze, kein Raster. Nur wenn ein einzelner Absatz die
   Obergrenze sprengt, wird er - mit Ueberlappung - geteilt.
2. **Kein Chunk ohne Provenienz.** Dateiname immer, Seitenzahl bei allen
   Formaten mit Seiten (PDF), Abschnitt wenn erkennbar. Ein Chunk ohne
   Provenienz waere spaeter nicht zitierfaehig und darf gar nicht erst
   entstehen - ``pruefe_provenienz`` setzt das durch.

Deshalb laeuft die Zerlegung strikt seitenweise: Ein Chunk gehoert zu genau
einer Seite. Ein Abschnitt, der ueber den Seitenumbruch laeuft, wird lieber
geteilt, als dass eine Fundstelle eine ungenaue Seitenzahl bekommt.
"""

import logging
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from . import config
from .dokumente import DokumentInhalt, Seite

logger = logging.getLogger(__name__)


class ProvenienzFehler(ValueError):
    """Einem Chunk fehlt die Herkunft - er darf nicht gespeichert werden."""


@dataclass(frozen=True)
class Chunk:
    """Ein Textausschnitt mit allem, was eine Fundstelle braucht."""

    text: str
    dateiname: str
    seite: Optional[int]
    abschnitt: Optional[str]
    position: int


# Nummerierte Ueberschriften: "3", "3.1", "03.02.040", "§ 6", "Anlage 2".
_NUMMERIERUNG = re.compile(
    r"^(?:§\s*\d+|\d{1,3}(?:\.\d{1,3})*[.)]?|[A-Z][.)]|(?:Anlage|Anhang|Kapitel|Abschnitt)\s+\w+)"
    r"(?:\s+\S|$)"
)
_MARKDOWN_UEBERSCHRIFT = re.compile(r"^#{1,6}\s+")
# Satzende fuer das Teilen ueberlanger Absaetze.
_SATZGRENZE = re.compile(r"(?<=[.!?:;])\s+")
_SCHLUSSZEICHEN = (".", ",", ";", ":", "!", "?")


def ist_ueberschrift(zeile: str) -> bool:
    """Heuristik: Ist diese Zeile eine Ueberschrift?

    Bewusst konservativ. Eine falsch erkannte Ueberschrift zerschneidet einen
    Absatz, eine nicht erkannte kostet nur den Abschnittsnamen in der
    Fundstelle - der zweite Fehler ist der harmlosere.
    """
    text = zeile.strip()
    if not text or len(text) > 120:
        return False
    if _MARKDOWN_UEBERSCHRIFT.match(text):
        return True
    if text.endswith(_SCHLUSSZEICHEN):
        return False
    woerter = text.split()
    if len(woerter) > 14:
        return False
    if _NUMMERIERUNG.match(text):
        return True
    # Versalien-Ueberschrift ("ALLGEMEINE VORBEMERKUNGEN") - nur mit Buchstaben.
    return text.isupper() and any(zeichen.isalpha() for zeichen in text) and len(text) > 3


def _ueberschrift_text(zeile: str) -> str:
    return _MARKDOWN_UEBERSCHRIFT.sub("", zeile.strip()).strip()


@dataclass
class _Block:
    """Ein Absatz oder eine Ueberschrift mit dem Abschnitt, in dem er steht."""

    text: str
    ueberschrift: Optional[str]


def _bloecke(seitentext: str) -> list[_Block]:
    """Seitentext in Absaetze zerlegen und Ueberschriften zuordnen."""
    bloecke: list[_Block] = []
    aktuelle_ueberschrift: Optional[str] = None
    absatz: list[str] = []

    def absatz_abschliessen() -> None:
        if absatz:
            bloecke.append(_Block("\n".join(absatz).strip(), aktuelle_ueberschrift))
            absatz.clear()

    for zeile in seitentext.split("\n"):
        if not zeile.strip():
            absatz_abschliessen()
            continue
        if ist_ueberschrift(zeile):
            absatz_abschliessen()
            aktuelle_ueberschrift = _ueberschrift_text(zeile)
            continue
        absatz.append(zeile.strip())

    absatz_abschliessen()
    return [block for block in bloecke if block.text]


def _teile_langen_absatz(text: str, max_zeichen: int, ueberlappung: int) -> list[str]:
    """Ueberlangen Absatz an Satzgrenzen teilen, mit Ueberlappung.

    Die Ueberlappung sorgt dafuer, dass eine Aussage, die genau auf der
    Schnittkante steht, in mindestens einem Chunk vollstaendig vorkommt und
    damit woertlich zitiert werden kann.
    """
    saetze = [satz for satz in _SATZGRENZE.split(text) if satz.strip()]
    teile: list[str] = []
    puffer: list[str] = []
    laenge = 0

    def puffer_text() -> str:
        return " ".join(puffer).strip()

    for satz in saetze:
        satz = satz.strip()
        while len(satz) > max_zeichen:
            # Ein einzelner "Satz" laenger als das Maximum (z.B. eine
            # Tabellenzeile ohne Satzzeichen) - hart schneiden, sonst nie fertig.
            if puffer:
                teile.append(puffer_text())
                puffer, laenge = [], 0
            teile.append(satz[:max_zeichen].strip())
            satz = satz[max(0, max_zeichen - ueberlappung) :].strip()
        if laenge + len(satz) + 1 > max_zeichen and puffer:
            teile.append(puffer_text())
            # Ueberlappung: die letzten Saetze mitnehmen, bis das Budget voll ist.
            uebernahme: list[str] = []
            uebernommen = 0
            for vorheriger in reversed(puffer):
                if uebernommen + len(vorheriger) > ueberlappung:
                    break
                uebernahme.insert(0, vorheriger)
                uebernommen += len(vorheriger) + 1
            puffer = uebernahme
            laenge = uebernommen
        puffer.append(satz)
        laenge += len(satz) + 1

    if puffer_text():
        teile.append(puffer_text())
    return teile


def _seite_zu_chunks(
    seite: Seite,
    dateiname: str,
    start_position: int,
    *,
    max_zeichen: int,
    min_zeichen: int,
    ueberlappung: int,
) -> list[Chunk]:
    bloecke = _bloecke(seite.text)
    if not bloecke:
        return []

    rohchunks: list[tuple[str, Optional[str]]] = []
    puffer: list[str] = []
    puffer_ueberschrift: Optional[str] = None

    def puffer_leeren() -> None:
        nonlocal puffer
        inhalt = "\n\n".join(puffer).strip()
        if inhalt:
            rohchunks.append((inhalt, puffer_ueberschrift))
        puffer = []

    for block in bloecke:
        if block.ueberschrift != puffer_ueberschrift:
            # Neuer Abschnitt = natuerliche Grenze.
            puffer_leeren()
            puffer_ueberschrift = block.ueberschrift

        for teil in (
            _teile_langen_absatz(block.text, max_zeichen, ueberlappung)
            if len(block.text) > max_zeichen
            else [block.text]
        ):
            aktuelle_laenge = sum(len(eintrag) + 2 for eintrag in puffer)
            if puffer and aktuelle_laenge + len(teil) > max_zeichen:
                puffer_leeren()
            puffer.append(teil)

    puffer_leeren()

    # Zu kurze Reste mit dem Vorgaenger verschmelzen, statt Schnipsel zu
    # indexieren (Kopfzeilen, "Seite 3 von 40"). Was sich nicht verschmelzen
    # laesst und zu kurz bleibt, traegt nichts zur Beantwortung bei.
    zusammengefasst: list[tuple[str, Optional[str]]] = []
    for text, ueberschrift in rohchunks:
        if (
            zusammengefasst
            and len(text) < min_zeichen
            and zusammengefasst[-1][1] == ueberschrift
            and len(zusammengefasst[-1][0]) + len(text) <= max_zeichen
        ):
            vorheriger = zusammengefasst.pop()
            zusammengefasst.append((f"{vorheriger[0]}\n\n{text}", ueberschrift))
        else:
            zusammengefasst.append((text, ueberschrift))

    chunks: list[Chunk] = []
    position = start_position
    for text, ueberschrift in zusammengefasst:
        # Kurze Reste ohne eigene Ueberschrift sind in aller Regel Kopf- und
        # Fusszeilen ("Seite 3 von 40"). Ein kurzer Abschnitt MIT Ueberschrift
        # ist dagegen echter Inhalt und bleibt - er muss zitierbar sein.
        if len(text) < min_zeichen and ueberschrift is None and len(zusammengefasst) > 1:
            continue
        # Die Ueberschrift steht im Chunktext, damit Bedeutungs- und
        # Volltextsuche den Abschnitt mitsehen ("03.02.040 Rohrgraben").
        volltext = f"{ueberschrift}\n\n{text}" if ueberschrift else text
        chunks.append(
            Chunk(
                text=volltext,
                dateiname=dateiname,
                seite=seite.nummer,
                abschnitt=ueberschrift,
                position=position,
            )
        )
        position += 1
    return chunks


def zerlege_dokument(
    inhalt: DokumentInhalt,
    *,
    max_zeichen: Optional[int] = None,
    min_zeichen: Optional[int] = None,
    ueberlappung: Optional[int] = None,
) -> list[Chunk]:
    """Ein eingelesenes Dokument in Chunks mit vollstaendiger Provenienz."""
    max_zeichen = max_zeichen or config.MIND_CHUNK_MAX_ZEICHEN
    min_zeichen = min_zeichen if min_zeichen is not None else config.MIND_CHUNK_MIN_ZEICHEN
    ueberlappung = (
        ueberlappung if ueberlappung is not None else config.MIND_CHUNK_UEBERLAPPUNG
    )
    # Die Ueberlappung muss deutlich kleiner sein als die Obergrenze, sonst
    # kommt das Teilen ueberlanger Absaetze nicht vom Fleck.
    ueberlappung = max(0, min(ueberlappung, max_zeichen // 2))

    chunks: list[Chunk] = []
    for seite in inhalt.seiten:
        chunks.extend(
            _seite_zu_chunks(
                seite,
                inhalt.dateiname,
                len(chunks),
                max_zeichen=max_zeichen,
                min_zeichen=min_zeichen,
                ueberlappung=ueberlappung,
            )
        )

    pruefe_provenienz(chunks, seiten_pflicht=inhalt.seiten_pflicht)
    logger.info(
        "%s: %s Seite(n) -> %s Chunk(s).", inhalt.dateiname, len(inhalt.seiten), len(chunks)
    )
    return chunks


def pruefe_provenienz(chunks: Iterable[Chunk], *, seiten_pflicht: bool) -> None:
    """Torwaechter vor der Datenbank: kein Chunk ohne Herkunft.

    Ohne Dateiname (und bei PDF ohne Seitenzahl) laesst sich spaeter keine
    Fundstelle angeben - und ohne Fundstelle gibt Mind keine Antwort. Solche
    Chunks duerfen deshalb gar nicht erst gespeichert werden.
    """
    for chunk in chunks:
        if not chunk.dateiname.strip():
            raise ProvenienzFehler("Chunk ohne Dateiname.")
        if not chunk.text.strip():
            raise ProvenienzFehler(f"{chunk.dateiname}: leerer Chunk.")
        if seiten_pflicht and chunk.seite is None:
            raise ProvenienzFehler(
                f"{chunk.dateiname}: Chunk ohne Seitenzahl (bei diesem Format Pflicht)."
            )

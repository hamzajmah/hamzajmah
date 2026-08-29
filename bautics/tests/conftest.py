"""Gemeinsame Test-Vorbereitung.

Wichtig: Die Umgebungsvariablen muessen stehen, BEVOR ``app.config`` importiert
wird - die Konfiguration liest sie beim Import. Kein Test spricht mit einem
echten Dienst; alle Aussenkontakte sind Attrappen.
"""

import hashlib
import os
import re
import tempfile

_TESTABLAGE = tempfile.mkdtemp(prefix="bautics-tests-")

os.environ.setdefault("BAUTICS_STORAGE_DIR", os.path.join(_TESTABLAGE, "storage"))
os.environ.setdefault("BAUTICS_KNOWLEDGE_DIR", os.path.join(_TESTABLAGE, "wissensbank"))
os.environ.setdefault("BAUTICS_DATABASE_URL", f"sqlite:///{_TESTABLAGE}/bautics.db")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("DEEPGRAM_API_KEY", "test-deepgram-key")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "AC-test")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test-twilio-token")
os.environ.setdefault("BAUTICS_BASE_URL", "https://echo.bautics.test")
# Kurze Testvektoren - die echte Laenge des Embedding-Modells braucht hier niemand.
os.environ.setdefault("BAUTICS_EMBEDDING_DIMENSIONEN", "64")

import pytest  # noqa: E402

from app import db  # noqa: E402


@pytest.fixture
def datenbank(tmp_path):
    """Frische SQLite-Datenbank je Test."""
    engine = db.erzeuge_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.setze_engine(engine)
    db.init_db()
    yield engine
    engine.dispose()


# --- Hilfen fuer Mind ------------------------------------------------------

TEST_DIMENSIONEN = 64


def pseudo_vektor(text: str, dimensionen: int = TEST_DIMENSIONEN) -> list[float]:
    """Deterministischer Ersatz fuer echte Embeddings.

    Zaehlt Woerter in Faecher - aehnlicher Wortschatz ergibt aehnliche
    Vektoren. Zahlen und Positionsnummern werden bewusst ignoriert: Genau
    diese Schwaeche der Bedeutungssuche ist der Grund fuer die zusaetzliche
    Volltextsuche, und die Tests sollen sie abbilden.
    """
    vektor = [0.0] * dimensionen
    # Nur Buchstabenfolgen - keine Ziffern, keine Positionsnummern.
    for wort in re.findall(r"[^\W\d_]{3,}", text.lower(), re.UNICODE):
        fach = int(hashlib.md5(wort.encode("utf-8")).hexdigest(), 16) % dimensionen
        vektor[fach] += 1.0
    if not any(vektor):
        vektor[0] = 1.0
    return vektor


def pseudo_vektoren(texte: list[str]) -> list[list[float]]:
    return [pseudo_vektor(text) for text in texte]


def _pdf_escape(text: str) -> bytes:
    roh = text.encode("latin-1", errors="replace")
    return roh.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def baue_pdf(seiten: list[str]) -> bytes:
    """Minimales, gueltiges PDF mit echtem Textinhalt je Seite.

    Bewusst von Hand gebaut statt mit einer weiteren Abhaengigkeit - die Tests
    brauchen nur extrahierbaren Text auf nummerierten Seiten.
    """
    objekte: dict[int, bytes] = {}
    seiten_ids = [4 + 2 * index for index in range(len(seiten))]

    objekte[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = b" ".join(f"{nummer} 0 R".encode("ascii") for nummer in seiten_ids)
    objekte[2] = (
        b"<< /Type /Pages /Kids [" + kids + b"] /Count "
        + str(len(seiten)).encode("ascii") + b" >>"
    )
    objekte[3] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )

    for index, seitentext in enumerate(seiten):
        seiten_id = seiten_ids[index]
        inhalt_id = seiten_id + 1
        zeilen = seitentext.split("\n")
        strom = b"BT /F1 11 Tf 56 780 Td 14 TL\n"
        for zeile in zeilen:
            strom += b"(" + _pdf_escape(zeile) + b") Tj T*\n"
        strom += b"ET"
        objekte[seiten_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            + str(inhalt_id).encode("ascii") + b" 0 R >>"
        )
        objekte[inhalt_id] = (
            b"<< /Length " + str(len(strom)).encode("ascii") + b" >>\nstream\n"
            + strom + b"\nendstream"
        )

    kopf = b"%PDF-1.4\n"
    koerper = b""
    versaetze: dict[int, int] = {}
    for nummer in sorted(objekte):
        versaetze[nummer] = len(kopf) + len(koerper)
        koerper += (
            str(nummer).encode("ascii") + b" 0 obj\n" + objekte[nummer] + b"\nendobj\n"
        )

    xref_start = len(kopf) + len(koerper)
    anzahl = max(objekte) + 1
    xref = b"xref\n0 " + str(anzahl).encode("ascii") + b"\n0000000000 65535 f \n"
    for nummer in range(1, anzahl):
        versatz = versaetze.get(nummer, 0)
        xref += f"{versatz:010d} 00000 n \n".encode("ascii")
    ende = (
        b"trailer\n<< /Size " + str(anzahl).encode("ascii") + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_start).encode("ascii") + b"\n%%EOF\n"
    )
    return kopf + koerper + xref + ende


@pytest.fixture
def wissensbank(tmp_path):
    """Leerer Wissensbank-Ordner je Test."""
    ordner = tmp_path / "wissensbank"
    ordner.mkdir()
    return ordner


def lege_chunks_an(
    dateiname: str,
    texte: list[str],
    *,
    projekt: str = "SuedLink Baulos 4",
    dokumenttyp: str = "Leistungsverzeichnis",
    seiten: list[int] | None = None,
    abschnitte: list[str | None] | None = None,
) -> list[int]:
    """Chunks direkt anlegen - fuer Suchtests ohne Indexlauf."""
    import datetime as dt

    with db.sitzung() as sitzung:
        dokument = db.WissensDokument(
            pfad=f"{projekt}/{dateiname}",
            dateiname=dateiname,
            dateiformat="pdf",
            dokumenttyp=dokumenttyp,
            projekt=projekt,
            datei_hash="testhash",
            geaendert_am=dt.datetime(2026, 8, 1),
            seiten_anzahl=len(texte),
            chunk_anzahl=len(texte),
        )
        sitzung.add(dokument)
        sitzung.flush()
        ids: list[int] = []
        for position, text in enumerate(texte):
            chunk = db.WissensChunk(
                dokument_id=dokument.id,
                dateiname=dateiname,
                projekt=projekt,
                dokumenttyp=dokumenttyp,
                seite=(seiten[position] if seiten else position + 1),
                abschnitt=(abschnitte[position] if abschnitte else None),
                position=position,
                text=text,
                text_klein=text.lower(),
                embedding=pseudo_vektor(text),
            )
            sitzung.add(chunk)
            sitzung.flush()
            ids.append(chunk.id)
    return ids

"""Echo - Ablauflogik: Sprachnachricht -> fertiger Tagesbericht.

Dieses Modul kennt weder FastAPI noch Twilio-Signaturen. Es bekommt eine
bereits geprueften Nachricht und ist damit isoliert testbar; alle externen
Schritte (Download, Spracherkennung, Engine, Rueckmeldung) haengen an
``EchoDienste`` und lassen sich in Tests ersetzen.
"""

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Callable, Mapping, Optional

from sqlalchemy.exc import IntegrityError

from . import config, db, report, strukturierung, stt, twilio_api
from .schemas import TagesberichtDaten

logger = logging.getLogger(__name__)

ANTWORT_KEINE_AUDIODATEI = (
    "Bitte schick mir eine Sprachnachricht – aus Text erstelle ich (noch) "
    "keinen Tagesbericht."
)
ANTWORT_ANGENOMMEN = "Sprachnachricht erhalten. Ich erstelle den Tagesbericht …"
ANTWORT_DOPPELT = "Diese Sprachnachricht habe ich bereits verarbeitet."
ANTWORT_FEHLER = (
    "Der Tagesbericht konnte nicht erstellt werden. Bitte die Sprachnachricht "
    "noch einmal senden oder im Büro melden."
)


def maskiere_nummer(nummer: str) -> str:
    """Telefonnummer fuer Logs unkenntlich machen (Kundendaten)."""
    ziffern = "".join(zeichen for zeichen in nummer if zeichen.isdigit())
    if len(ziffern) < 4:
        return "***"
    return f"***{ziffern[-4:]}"


@dataclass(frozen=True)
class EingehendeNachricht:
    """Das, was von einem Twilio-Webhook fachlich uebrig bleibt."""

    nachricht_sid: str
    absender: str
    medien_url: Optional[str] = None
    medien_typ: str = ""
    text: str = ""

    @property
    def hat_audio(self) -> bool:
        return bool(self.medien_url) and self.medien_typ.startswith("audio")

    @classmethod
    def aus_twilio_formular(cls, felder: Mapping[str, str]) -> "EingehendeNachricht":
        """Twilio-Formularfelder auf unser Modell abbilden.

        Twilio nummeriert Anhaenge (MediaUrl0, MediaContentType0, ...). Wir
        nehmen den ersten Audioanhang; alles andere ignorieren wir bewusst.
        """
        try:
            anzahl_medien = int(felder.get("NumMedia", "0"))
        except ValueError:
            anzahl_medien = 0

        medien_url: Optional[str] = None
        medien_typ = ""
        for index in range(anzahl_medien):
            typ = (felder.get(f"MediaContentType{index}") or "").split(";")[0].strip()
            url = felder.get(f"MediaUrl{index}")
            if url and typ.startswith("audio"):
                medien_url, medien_typ = url, typ
                break

        return cls(
            nachricht_sid=(felder.get("MessageSid") or "").strip(),
            absender=(felder.get("From") or "").strip(),
            medien_url=medien_url,
            medien_typ=medien_typ,
            text=(felder.get("Body") or "").strip(),
        )


@dataclass
class EchoDienste:
    """Austauschbare Aussenkontakte - in Tests durch Attrappen ersetzbar."""

    lade_medium: Callable[[str], tuple[bytes, str]] = twilio_api.lade_medium
    transkribiere: Callable[[bytes, str], str] = stt.transkribiere
    strukturiere: Callable[[str], TagesberichtDaten] = (
        strukturierung.strukturiere_transkript
    )
    sende_antwort: Callable[[str, str], None] = twilio_api.sende_whatsapp
    heute: Callable[[], dt.date] = dt.date.today
    projekt_fuer_absender: Callable[[str], str] = config.projekt_fuer_absender


@dataclass(frozen=True)
class Annahme:
    """Ergebnis der synchronen Annahme im Webhook."""

    antwort_text: str
    bericht_id: Optional[int] = None
    medien_url: Optional[str] = None
    # False bei Textnachrichten und bei erneut zugestellten Webhooks.
    verarbeiten: bool = False


def nimm_nachricht_an(
    nachricht: EingehendeNachricht,
    dienste: Optional[EchoDienste] = None,
) -> Annahme:
    """Nachricht pruefen, idempotent registrieren, Sofortantwort bestimmen.

    Bewusst schnell und ohne externe Aufrufe: Twilio erwartet zuegig eine
    Antwort, die eigentliche Verarbeitung laeuft danach im Hintergrund.
    """
    dienste = dienste or EchoDienste()

    if not nachricht.nachricht_sid:
        raise ValueError("Nachricht ohne MessageSid - Idempotenz nicht moeglich.")
    if not nachricht.hat_audio:
        logger.info("Nachricht ohne Audioanhang von %s.", maskiere_nummer(nachricht.absender))
        return Annahme(antwort_text=ANTWORT_KEINE_AUDIODATEI)

    try:
        with db.sitzung() as sitzung:
            vorhanden = db.finde_nach_sid(sitzung, nachricht.nachricht_sid)
            if vorhanden is not None:
                return _doppelt(vorhanden)

            bericht = db.Tagesbericht(
                nachricht_sid=nachricht.nachricht_sid,
                absender=nachricht.absender,
                projekt=dienste.projekt_fuer_absender(nachricht.absender),
                # TODO: Nachtschichten/spaete Meldungen ggf. auf den Vortag buchen.
                datum=dienste.heute(),
                status=db.STATUS_EMPFANGEN,
            )
            sitzung.add(bericht)
            sitzung.flush()
            bericht_id = bericht.id
    except IntegrityError:
        # Zwei Zustellungen desselben Webhooks gleichzeitig - die eindeutige
        # MessageSid haelt dagegen, der schnellere Lauf verarbeitet.
        with db.sitzung() as sitzung:
            vorhanden = db.finde_nach_sid(sitzung, nachricht.nachricht_sid)
        if vorhanden is None:
            raise
        return _doppelt(vorhanden)

    logger.info("Sprachnachricht angenommen, Bericht %s angelegt.", bericht_id)
    return Annahme(
        antwort_text=ANTWORT_ANGENOMMEN,
        bericht_id=bericht_id,
        medien_url=nachricht.medien_url,
        verarbeiten=True,
    )


def _doppelt(bericht: db.Tagesbericht) -> Annahme:
    logger.info(
        "Webhook-Wiederholung fuer Bericht %s (Status %s) - ignoriert.",
        bericht.id,
        bericht.status,
    )
    return Annahme(antwort_text=ANTWORT_DOPPELT, bericht_id=bericht.id)


def verarbeite_bericht(
    bericht_id: int,
    medien_url: str,
    dienste: Optional[EchoDienste] = None,
    *,
    antwort_senden: bool = True,
) -> str:
    """Audio holen, transkribieren, strukturieren, Bericht rendern und sichern.

    Gibt den fertigen Berichtstext zurueck. Fehler werden am Bericht vermerkt
    und - soweit moeglich - dem Bauleiter zurueckgemeldet.
    """
    dienste = dienste or EchoDienste()

    try:
        audio, inhaltstyp = dienste.lade_medium(medien_url)
        transkript = dienste.transkribiere(audio, inhaltstyp)
        daten = dienste.strukturiere(transkript)
    except Exception as fehler:  # noqa: BLE001 - jeder Fehler gehoert an den Bericht
        _vermerke_fehler(bericht_id, fehler)
        if antwort_senden:
            _antwort_versuchen(bericht_id, ANTWORT_FEHLER, dienste)
        raise

    with db.sitzung() as sitzung:
        bericht = sitzung.get(db.Tagesbericht, bericht_id)
        if bericht is None:
            raise LookupError(f"Bericht {bericht_id} nicht gefunden.")

        if bericht.berichtsnummer is None:
            bericht.berichtsnummer = db.naechste_berichtsnummer(
                sitzung, bericht.projekt, bericht.datum
            )
        meta = report.BerichtMetadaten(
            projekt=bericht.projekt,
            datum=bericht.datum,
            berichtsnummer=bericht.berichtsnummer,
        )
        berichtstext = report.rendere_tagesbericht(daten, meta)

        bericht.rohtranskript = transkript
        bericht.daten_json = daten.model_dump(mode="json")
        bericht.berichtstext = berichtstext
        bericht.status = db.STATUS_FERTIG
        bericht.fehlermeldung = None
        absender = bericht.absender
        nummer = bericht.berichtsnummer

    logger.info("Bericht %s fertiggestellt (Nr. %s).", bericht_id, nummer)

    if antwort_senden:
        kopf = (
            f"Tagesbericht Nr. {nummer:03d} – {report.kurzfassung(daten)}.\n"
            "Bitte prüfen und freigeben.\n\n"
        )
        _antwort_versuchen(bericht_id, kopf + berichtstext, dienste, empfaenger=absender)

    return berichtstext


def _vermerke_fehler(bericht_id: int, fehler: Exception) -> None:
    """Fehler am Bericht festhalten - Meldung ohne Inhalte der Sprachnachricht."""
    meldung = f"{type(fehler).__name__}: {fehler}"
    logger.error("Verarbeitung von Bericht %s fehlgeschlagen: %s", bericht_id, meldung)
    try:
        with db.sitzung() as sitzung:
            bericht = sitzung.get(db.Tagesbericht, bericht_id)
            if bericht is not None:
                bericht.status = db.STATUS_FEHLER
                bericht.fehlermeldung = meldung[:1000]
    except Exception:  # noqa: BLE001 - Fehlerpfad darf nicht selbst platzen
        logger.exception("Fehlerstatus fuer Bericht %s konnte nicht gespeichert werden.", bericht_id)


def _antwort_versuchen(
    bericht_id: int,
    text: str,
    dienste: EchoDienste,
    empfaenger: Optional[str] = None,
) -> None:
    """WhatsApp-Rueckmeldung - ein Fehler hier darf den Bericht nicht kippen."""
    try:
        if empfaenger is None:
            with db.sitzung() as sitzung:
                bericht = sitzung.get(db.Tagesbericht, bericht_id)
                empfaenger = bericht.absender if bericht is not None else None
        if not empfaenger:
            logger.warning("Kein Empfaenger fuer Bericht %s bekannt.", bericht_id)
            return
        dienste.sende_antwort(empfaenger, text)
    except Exception:  # noqa: BLE001
        logger.exception("Rueckmeldung zu Bericht %s konnte nicht gesendet werden.", bericht_id)

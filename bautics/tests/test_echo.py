"""Ablauflogik von Echo - ohne Twilio, ohne Netz."""

import datetime as dt

import pytest

from app import db, echo
from app.echo import (
    ANTWORT_DOPPELT,
    ANTWORT_FEHLER,
    ANTWORT_KEINE_AUDIODATEI,
    EchoDienste,
    EingehendeNachricht,
    maskiere_nummer,
    nimm_nachricht_an,
    verarbeite_bericht,
)
from app.schemas import Leistung, TagesberichtDaten

ABSENDER = "whatsapp:+4915112345678"

FORMULAR_MIT_AUDIO = {
    "MessageSid": "SM1111",
    "From": ABSENDER,
    "NumMedia": "1",
    "MediaUrl0": "https://api.twilio.com/media/1",
    "MediaContentType0": "audio/ogg",
    "Body": "",
}


def _dienste(gesendet: list[tuple[str, str]], daten: TagesberichtDaten | None = None):
    ergebnis = daten or TagesberichtDaten(
        personal_gewerblich=8,
        leistungen=[Leistung(beschreibung="Rohrgraben ausgehoben", station_von="12+400")],
    )
    return EchoDienste(
        lade_medium=lambda url: (b"audiobytes", "audio/ogg"),
        transkribiere=lambda audio, typ: "Acht Mann, Rohrgraben ab zwoelf plus vierhundert.",
        strukturiere=lambda transkript: ergebnis,
        sende_antwort=lambda an, text: gesendet.append((an, text)),
        heute=lambda: dt.date(2026, 8, 28),
        projekt_fuer_absender=lambda absender: "SuedLink Baulos 4",
    )


def test_formular_wird_auf_fachobjekt_abgebildet():
    nachricht = EingehendeNachricht.aus_twilio_formular(FORMULAR_MIT_AUDIO)

    assert nachricht.nachricht_sid == "SM1111"
    assert nachricht.absender == ABSENDER
    assert nachricht.hat_audio
    assert nachricht.medien_typ == "audio/ogg"


def test_bild_gilt_nicht_als_sprachnachricht():
    nachricht = EingehendeNachricht.aus_twilio_formular(
        {
            "MessageSid": "SM2222",
            "From": ABSENDER,
            "NumMedia": "1",
            "MediaUrl0": "https://api.twilio.com/media/2",
            "MediaContentType0": "image/jpeg",
        }
    )

    assert not nachricht.hat_audio


def test_reine_textnachricht_wird_freundlich_abgewiesen(datenbank):
    nachricht = EingehendeNachricht.aus_twilio_formular(
        {"MessageSid": "SM3333", "From": ABSENDER, "NumMedia": "0", "Body": "Moin"}
    )

    annahme = nimm_nachricht_an(nachricht, _dienste([]))

    assert annahme.antwort_text == ANTWORT_KEINE_AUDIODATEI
    assert annahme.verarbeiten is False
    with db.sitzung() as sitzung:
        assert sitzung.query(db.Tagesbericht).count() == 0


def test_annahme_legt_bericht_an(datenbank):
    annahme = nimm_nachricht_an(
        EingehendeNachricht.aus_twilio_formular(FORMULAR_MIT_AUDIO), _dienste([])
    )

    assert annahme.verarbeiten
    assert annahme.medien_url == "https://api.twilio.com/media/1"
    with db.sitzung() as sitzung:
        bericht = sitzung.get(db.Tagesbericht, annahme.bericht_id)
        assert bericht.status == db.STATUS_EMPFANGEN
        assert bericht.projekt == "SuedLink Baulos 4"
        assert bericht.datum == dt.date(2026, 8, 28)


def test_webhook_wiederholung_ist_idempotent(datenbank):
    nachricht = EingehendeNachricht.aus_twilio_formular(FORMULAR_MIT_AUDIO)
    erste = nimm_nachricht_an(nachricht, _dienste([]))
    zweite = nimm_nachricht_an(nachricht, _dienste([]))

    assert zweite.verarbeiten is False
    assert zweite.antwort_text == ANTWORT_DOPPELT
    assert zweite.bericht_id == erste.bericht_id
    with db.sitzung() as sitzung:
        assert sitzung.query(db.Tagesbericht).count() == 1


def test_gleichzeitige_zustellung_bleibt_idempotent(datenbank, monkeypatch):
    """Rennen zwischen zwei Zustellungen: die eindeutige MessageSid haelt dagegen."""
    dienste = _dienste([])
    nachricht = EingehendeNachricht.aus_twilio_formular(FORMULAR_MIT_AUDIO)
    erste = nimm_nachricht_an(nachricht, dienste)

    echte_suche = db.finde_nach_sid
    aufrufe: list[str] = []

    def sucht_beim_ersten_mal_nichts(sitzung, sid):
        aufrufe.append(sid)
        return None if len(aufrufe) == 1 else echte_suche(sitzung, sid)

    monkeypatch.setattr(db, "finde_nach_sid", sucht_beim_ersten_mal_nichts)

    zweite = nimm_nachricht_an(nachricht, dienste)

    assert zweite.verarbeiten is False
    assert zweite.bericht_id == erste.bericht_id
    with db.sitzung() as sitzung:
        assert sitzung.query(db.Tagesbericht).count() == 1


def test_nachricht_ohne_sid_wird_abgelehnt(datenbank):
    with pytest.raises(ValueError):
        nimm_nachricht_an(EingehendeNachricht(nachricht_sid="", absender=ABSENDER))


def test_vollstaendiger_durchstich(datenbank):
    gesendet: list[tuple[str, str]] = []
    dienste = _dienste(gesendet)
    annahme = nimm_nachricht_an(
        EingehendeNachricht.aus_twilio_formular(FORMULAR_MIT_AUDIO), dienste
    )

    text = verarbeite_bericht(annahme.bericht_id, annahme.medien_url, dienste)

    assert "Rohrgraben ausgehoben" in text
    assert "001/2026" in text
    with db.sitzung() as sitzung:
        bericht = sitzung.get(db.Tagesbericht, annahme.bericht_id)
        assert bericht.status == db.STATUS_FERTIG
        assert bericht.berichtsnummer == 1
        assert bericht.rohtranskript.startswith("Acht Mann")
        assert bericht.daten_json["leistungen"][0]["station_von"] == "12+400"
        assert bericht.berichtstext == text
        assert bericht.fehlermeldung is None

    assert len(gesendet) == 1
    empfaenger, antwort = gesendet[0]
    assert empfaenger == ABSENDER
    assert "Tagesbericht Nr. 001" in antwort


def test_berichtsnummer_laeuft_je_baulos_fort(datenbank):
    gesendet: list[tuple[str, str]] = []
    dienste = _dienste(gesendet)
    nummern = []
    for lauf, sid in enumerate(("SM-A", "SM-B")):
        formular = dict(FORMULAR_MIT_AUDIO, MessageSid=sid)
        annahme = nimm_nachricht_an(
            EingehendeNachricht.aus_twilio_formular(formular), dienste
        )
        verarbeite_bericht(annahme.bericht_id, annahme.medien_url, dienste)
        with db.sitzung() as sitzung:
            nummern.append(sitzung.get(db.Tagesbericht, annahme.bericht_id).berichtsnummer)

    assert nummern == [1, 2]


def test_fehler_landet_am_bericht_und_beim_bauleiter(datenbank):
    gesendet: list[tuple[str, str]] = []
    dienste = _dienste(gesendet)

    def kaputt(audio, typ):
        raise RuntimeError("Spracherkennung nicht erreichbar")

    dienste.transkribiere = kaputt
    annahme = nimm_nachricht_an(
        EingehendeNachricht.aus_twilio_formular(FORMULAR_MIT_AUDIO), dienste
    )

    with pytest.raises(RuntimeError):
        verarbeite_bericht(annahme.bericht_id, annahme.medien_url, dienste)

    with db.sitzung() as sitzung:
        bericht = sitzung.get(db.Tagesbericht, annahme.bericht_id)
        assert bericht.status == db.STATUS_FEHLER
        assert "Spracherkennung nicht erreichbar" in bericht.fehlermeldung
        assert bericht.berichtstext is None

    assert gesendet == [(ABSENDER, ANTWORT_FEHLER)]


def test_leeres_transkript_erzeugt_keinen_erfundenen_inhalt(datenbank):
    """Sagt der Bauleiter nichts Konkretes, bleibt der Bericht leer."""
    gesendet: list[tuple[str, str]] = []
    dienste = _dienste(gesendet, daten=TagesberichtDaten())
    annahme = nimm_nachricht_an(
        EingehendeNachricht.aus_twilio_formular(FORMULAR_MIT_AUDIO), dienste
    )

    text = verarbeite_bericht(annahme.bericht_id, annahme.medien_url, dienste)

    assert "keine Angabe" in text
    with db.sitzung() as sitzung:
        bericht = sitzung.get(db.Tagesbericht, annahme.bericht_id)
        assert bericht.daten_json["geraete"] == []
        assert bericht.daten_json["leistungen"] == []


def test_telefonnummern_werden_fuer_logs_maskiert():
    assert maskiere_nummer(ABSENDER) == "***5678"
    assert maskiere_nummer("") == "***"
    assert "4915112345678" not in maskiere_nummer(ABSENDER)


def test_standarddienste_zeigen_auf_echte_bausteine():
    """Ohne Attrappen haengen die Dienste an den produktiven Funktionen."""
    dienste = EchoDienste()

    assert dienste.transkribiere is echo.stt.transkribiere
    assert dienste.strukturiere is echo.strukturierung.strukturiere_transkript
    assert dienste.lade_medium is echo.twilio_api.lade_medium

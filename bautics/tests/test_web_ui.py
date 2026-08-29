"""Die Produkt-Oberflaeche: Zugangsschutz, Echo-Seiten, Mind-Seiten.

Geprueft wird, was ein Reviewer nicht durchgehen lassen wuerde: dass der
Login wirklich schuetzt, dass fehlende Angaben ehrlich leer bleiben, dass
"nichts gefunden" nicht wie ein Fehler aussieht - und dass nirgends ein
Modell- oder Anbietername in der Oberflaeche steht.
"""

import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient

from app import config, db, mind, report
from app.http_client import UpstreamFehler
from app.main import app
from app.openrouter import EngineFehler
from app.schemas import Ereignis, Fundstelle, Leistung, TagesberichtDaten
from app.web import indexlauf, sitzung

TOKEN = "test-zugangstoken-abcdef"


@pytest.fixture
def klient(datenbank, monkeypatch):
    """Client mit konfiguriertem Zugangstoken - also mit echtem Schutz."""
    monkeypatch.setattr(config, "API_TOKEN", TOKEN)
    # Das Sitzungs-Cookie wird nur ueber HTTPS gesetzt, wenn die oeffentliche
    # Basis-URL https ist (siehe sitzung.setze_cookie). Der Testclient spricht
    # http - sonst schickt er das Cookie nie zurueck.
    monkeypatch.setattr(config, "BASE_URL", "http://testserver")
    sitzung._versuche.clear()
    indexlauf.zuruecksetzen()
    with TestClient(app, follow_redirects=False) as klient:
        yield klient


@pytest.fixture
def angemeldet(klient):
    antwort = klient.post("/anmelden", data={"token": TOKEN, "weiter": "/echo"})
    assert antwort.status_code == 303
    return klient


def lege_bericht_an(
    *,
    sid: str,
    projekt: str = "SuedLink Baulos 4",
    datum: dt.date = dt.date(2026, 8, 27),
    nummer: int | None = 7,
    status: str = db.STATUS_FERTIG,
    daten: TagesberichtDaten | None = None,
    transkript: str | None = "Heute waren wir am Graben bei Station zwölf plus vier.",
) -> int:
    daten = daten if daten is not None else TagesberichtDaten()
    text = report.rendere_tagesbericht(
        daten, report.BerichtMetadaten(projekt=projekt, datum=datum, berichtsnummer=nummer)
    )
    with db.sitzung() as offen:
        bericht = db.Tagesbericht(
            nachricht_sid=sid,
            absender="whatsapp:+4915100000000",
            projekt=projekt,
            datum=datum,
            berichtsnummer=nummer,
            status=status,
            rohtranskript=transkript,
            daten_json=daten.model_dump(mode="json"),
            berichtstext=text if status == db.STATUS_FERTIG else None,
        )
        offen.add(bericht)
        offen.flush()
        return bericht.id


# --- Zugangsschutz ----------------------------------------------------------


def test_geschuetzte_seite_leitet_ohne_sitzung_zur_anmeldung(klient):
    antwort = klient.get("/echo")
    assert antwort.status_code == 303
    assert antwort.headers["location"].startswith("/anmelden?weiter=")
    # Die urspruenglich gewuenschte Seite wird gemerkt.
    assert "%2Fecho" in antwort.headers["location"]


def test_berichtsdetail_ist_ohne_sitzung_nicht_erreichbar(klient):
    bericht_id = lege_bericht_an(sid="SM-schutz")
    antwort = klient.get(f"/echo/{bericht_id}")
    assert antwort.status_code == 303
    assert "/anmelden" in antwort.headers["location"]
    # Kein Fetzen Inhalt darf durchrutschen.
    assert "SuedLink" not in antwort.text


def test_mind_seiten_sind_ohne_sitzung_gesperrt(klient):
    for pfad in ("/mind", "/mind/wissensbank"):
        assert klient.get(pfad).status_code == 303
    assert klient.post("/mind", data={"frage": "Wie lautet die Frist?"}).status_code == 303
    assert klient.post("/mind/wissensbank").status_code == 303


def test_falsches_token_wird_abgewiesen(klient):
    antwort = klient.post("/anmelden", data={"token": "falsch", "weiter": "/echo"})
    assert antwort.status_code == 401
    assert sitzung.COOKIE_NAME not in antwort.cookies
    assert "stimmt nicht" in antwort.text
    # Das eingegebene Token darf nirgends in der Seite auftauchen.
    assert "falsch" not in antwort.text


def test_richtiges_token_setzt_sitzung(klient):
    antwort = klient.post("/anmelden", data={"token": TOKEN, "weiter": "/echo"})
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/echo"
    keks = antwort.cookies.get(sitzung.COOKIE_NAME)
    assert keks and TOKEN not in keks  # das Token selbst wandert nicht ins Cookie
    assert klient.get("/echo").status_code == 200


def test_sitzungscookie_ist_httponly_und_samesite(klient):
    """Kein Zugriff aus JavaScript, kein Mitsenden bei fremden Formularen -
    letzteres ersetzt hier ein eigenes CSRF-Token."""
    kopf = klient.post("/anmelden", data={"token": TOKEN}).headers["set-cookie"].lower()
    assert "httponly" in kopf
    assert "samesite=lax" in kopf
    assert "secure" not in kopf  # im Test laeuft der Server ueber http


def test_sitzungscookie_ist_bei_https_secure(datenbank, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", TOKEN)
    monkeypatch.setattr(config, "BASE_URL", "https://bautics.beispiel")
    sitzung._versuche.clear()
    with TestClient(app, follow_redirects=False) as klient:
        kopf = klient.post("/anmelden", data={"token": TOKEN}).headers["set-cookie"].lower()
        assert "secure" in kopf


def test_abmelden_beendet_die_sitzung(angemeldet):
    assert angemeldet.get("/echo").status_code == 200
    angemeldet.post("/abmelden")
    assert angemeldet.get("/echo").status_code == 303


def test_manipuliertes_cookie_gilt_nicht(klient):
    echt = sitzung.baue_cookie()
    klient.cookies.set(sitzung.COOKIE_NAME, echt[:-4] + "aaaa")
    assert klient.get("/echo").status_code == 303


def test_abgelaufenes_cookie_gilt_nicht(klient):
    vergangen = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
    klient.cookies.set(sitzung.COOKIE_NAME, sitzung.baue_cookie(jetzt=vergangen))
    assert klient.get("/echo").status_code == 303


def test_cookie_wird_mit_dem_token_ungueltig(klient, monkeypatch):
    keks = sitzung.baue_cookie()
    assert sitzung.cookie_gueltig(keks)
    monkeypatch.setattr(config, "API_TOKEN", "ein-anderes-token")
    assert not sitzung.cookie_gueltig(keks)


def test_weiterleitung_nur_auf_eigene_pfade(klient):
    antwort = klient.post(
        "/anmelden", data={"token": TOKEN, "weiter": "https://beispiel.test/uebernahme"}
    )
    assert antwort.headers["location"] == "/echo"
    antwort = klient.post("/anmelden", data={"token": TOKEN, "weiter": "//beispiel.test"})
    assert antwort.headers["location"] == "/echo"


def test_bremse_greift_nach_zu_vielen_fehlversuchen(klient, monkeypatch):
    monkeypatch.setattr(config, "UI_ANMELDE_VERSUCHE", 3)
    for _ in range(3):
        assert klient.post("/anmelden", data={"token": "falsch"}).status_code == 401
    gesperrt = klient.post("/anmelden", data={"token": "falsch"})
    assert gesperrt.status_code == 429
    # Auch das richtige Token kommt jetzt nicht mehr durch.
    assert klient.post("/anmelden", data={"token": TOKEN}).status_code == 429


def test_ohne_konfiguriertes_token_gibt_es_kein_scheinformular(datenbank, monkeypatch):
    """Ein Login, der alles durchlaesst, taeuscht Schutz vor - stattdessen
    laeuft die Oberflaeche offen und sagt das deutlich."""
    monkeypatch.setattr(config, "API_TOKEN", "")
    with TestClient(app, follow_redirects=False) as klient:
        antwort = klient.get("/echo")
        assert antwort.status_code == 200
        assert "Ungeschützter Betrieb" in antwort.text
        assert klient.get("/anmelden").status_code == 303


# --- Echo: Liste ------------------------------------------------------------


def test_berichtsliste_zeigt_nummer_datum_baulos_status(angemeldet):
    lege_bericht_an(sid="SM-1", datum=dt.date(2026, 8, 25), nummer=5)
    lege_bericht_an(sid="SM-2", datum=dt.date(2026, 8, 27), nummer=7)
    antwort = angemeldet.get("/echo")
    assert antwort.status_code == 200
    assert "005/2026" in antwort.text
    assert "007/2026" in antwort.text
    assert "27.08.2026" in antwort.text
    assert "SuedLink Baulos 4" in antwort.text
    assert "Fertig" in antwort.text


def test_berichtsliste_neueste_zuerst(angemeldet):
    lege_bericht_an(sid="SM-alt", datum=dt.date(2026, 8, 20), nummer=3)
    lege_bericht_an(sid="SM-neu", datum=dt.date(2026, 8, 28), nummer=9)
    text = angemeldet.get("/echo").text
    assert text.index("009/2026") < text.index("003/2026")


def test_berichtsliste_laesst_sich_auf_ein_baulos_eingrenzen(angemeldet):
    lege_bericht_an(sid="SM-a", projekt="SuedLink Baulos 4", nummer=1)
    lege_bericht_an(sid="SM-b", projekt="Ostküstenleitung Los 2", nummer=2)
    text = angemeldet.get("/echo", params={"baulos": "Ostküstenleitung Los 2"}).text
    assert "002/2026" in text
    assert "001/2026" not in text


def test_leere_liste_erklaert_sich(angemeldet):
    antwort = angemeldet.get("/echo")
    assert antwort.status_code == 200
    assert "Noch keine Berichte" in antwort.text


def test_zustaende_stehen_immer_als_text_nicht_nur_als_farbe(angemeldet):
    lege_bericht_an(sid="SM-fehler", nummer=4, status=db.STATUS_FEHLER)
    lege_bericht_an(sid="SM-offen", nummer=5, status=db.STATUS_EMPFANGEN)
    text = angemeldet.get("/echo").text
    assert "Fehler" in text
    assert "In Arbeit" in text


# --- Echo: Detail -----------------------------------------------------------


def _voller_bericht() -> TagesberichtDaten:
    return TagesberichtDaten(
        personal_gewerblich=8,
        geraete=["2 Kettenbagger", "1 Raupe"],
        leistungen=[
            Leistung(
                beschreibung="Rohrgraben ausgehoben",
                station_von="12+400",
                station_bis="12+700",
            ),
            Leistung(beschreibung="Kabelschutzrohre verlegt"),
        ],
        ereignisse=[
            Ereignis(
                art="behinderung",
                beschreibung="Zufahrt durch Fremdgewerk blockiert",
                dauer_stunden=2.5,
                station_von="12+400",
            ),
            Ereignis(art="mehrleistung", beschreibung="Zusätzlicher Bodenaustausch"),
        ],
        vorschau="Morgen Fortsetzung Richtung Norden",
    )


def test_detail_zeigt_berichtstext_daten_und_transkript(angemeldet):
    bericht_id = lege_bericht_an(sid="SM-detail", daten=_voller_bericht())
    antwort = angemeldet.get(f"/echo/{bericht_id}")
    assert antwort.status_code == 200
    text = antwort.text
    assert "BAUTAGESBERICHT" in text          # der gerenderte Berichtstext
    assert "Rohrgraben ausgehoben" in text    # strukturierte Leistung
    assert "Station 12+400 bis 12+700" in text
    assert "2 Kettenbagger" in text
    assert "Rohtranskript" in text
    assert "Station zwölf plus vier" in text  # eingeklappt, aber vorhanden
    assert "<details" in text                 # und tatsaechlich einklappbar


def test_detail_unterscheidet_ereignisarten_mit_text_und_symbol(angemeldet):
    bericht_id = lege_bericht_an(sid="SM-ereignis", daten=_voller_bericht())
    text = angemeldet.get(f"/echo/{bericht_id}").text
    assert "Behinderung" in text
    assert "Mehrleistung" in text
    # Zustandsfarben aus DESIGN.md, aber nie ohne Beschriftung daneben.
    assert "border-l-crit" in text
    assert "border-l-warn" in text
    assert "2,5 h" in text


def test_detail_fuellt_fehlende_angaben_nicht_auf(angemeldet):
    """Nur die Leistung ist genannt - alles andere bleibt sichtbar leer."""
    daten = TagesberichtDaten(leistungen=[Leistung(beschreibung="Kabel gezogen")])
    bericht_id = lege_bericht_an(sid="SM-luecken", daten=daten, transkript=None)
    text = angemeldet.get(f"/echo/{bericht_id}").text
    assert report.LEER_MARKIERUNG in text
    assert "Keine besonderen Vorkommnisse gemeldet." in text
    # Keine erfundene Personalzahl, keine erfundene Stationierung.
    assert not re.search(r"Gewerbliche Mitarbeiter</p>\s*<p[^>]*>\s*\d", text)


def test_fehlgeschlagener_bericht_zeigt_keinen_technischen_rohtext(angemeldet):
    """Die Fehlermeldung aus der Datenbank ist Technikersprache und kann
    Dienstnamen enthalten - sie gehoert nicht in die Kundenoberflaeche."""
    bericht_id = lege_bericht_an(sid="SM-kaputt", status=db.STATUS_FEHLER)
    with db.sitzung() as offen:
        offen.get(db.Tagesbericht, bericht_id).fehlermeldung = (
            "UpstreamFehler: Spracherkennung: HTTP 500 bei whisper-1"
        )
    text = angemeldet.get(f"/echo/{bericht_id}").text
    assert "konnte nicht erstellt werden" in text
    assert "whisper" not in text.lower()
    assert "HTTP 500" not in text


def test_unbekannter_bericht_ergibt_404(angemeldet):
    antwort = angemeldet.get("/echo/999999")
    assert antwort.status_code == 404
    assert "Nicht gefunden" in antwort.text


# --- Mind -------------------------------------------------------------------


def test_mind_zeigt_fundstellen_prominent(angemeldet, monkeypatch):
    ergebnis = mind.MindErgebnis(
        gefunden=True,
        antwort="Die Behinderung ist unverzüglich schriftlich anzuzeigen.",
        fundstellen=[
            Fundstelle(
                datei="Bauvertrag_Los4.pdf",
                seite=17,
                abschnitt="§ 6 Behinderung",
                zitat=(
                    "Der Auftragnehmer hat die Behinderung unverzüglich "
                    "schriftlich anzuzeigen."
                ),
            )
        ],
        chunk_ids=[42],
    )
    monkeypatch.setattr(mind, "beantworte_frage", lambda *a, **k: ergebnis)

    antwort = angemeldet.post("/mind", data={"frage": "Wann ist eine Behinderung anzuzeigen?"})
    assert antwort.status_code == 200
    text = antwort.text
    assert "Fundstellen" in text
    assert "Bauvertrag_Los4.pdf" in text
    assert "Seite 17" in text
    assert "§ 6 Behinderung" in text
    assert "unverzüglich schriftlich anzuzeigen" in text


def test_mind_reicht_das_baulos_an_die_fachlogik_durch(angemeldet, monkeypatch):
    lege_bericht_an(sid="SM-los", projekt="Ostküstenleitung Los 2")
    gesehen: dict[str, object] = {}

    def merke(frage, *, baulos=None, **_):
        gesehen["frage"] = frage
        gesehen["baulos"] = baulos
        return mind.MindErgebnis(gefunden=False, antwort="", fundstellen=[], chunk_ids=[])

    monkeypatch.setattr(mind, "beantworte_frage", merke)
    angemeldet.post(
        "/mind",
        data={"frage": "Welche Massen sind ausgeschrieben?", "baulos": "Ostküstenleitung Los 2"},
    )
    assert gesehen["baulos"] == "Ostküstenleitung Los 2"


def test_unbekanntes_baulos_wird_nicht_stillschweigend_uebernommen(angemeldet, monkeypatch):
    gesehen: dict[str, object] = {}

    def merke(frage, *, baulos=None, **_):
        gesehen["baulos"] = baulos
        return mind.MindErgebnis(gefunden=False, antwort="", fundstellen=[], chunk_ids=[])

    monkeypatch.setattr(mind, "beantworte_frage", merke)
    angemeldet.post("/mind", data={"frage": "Eine Frage", "baulos": "Gibt es nicht"})
    assert gesehen["baulos"] is None


def test_nichts_gefunden_sieht_nicht_wie_ein_fehler_aus(angemeldet, monkeypatch):
    monkeypatch.setattr(
        mind,
        "beantworte_frage",
        lambda *a, **k: mind.MindErgebnis(
            gefunden=False, antwort="", fundstellen=[], chunk_ids=[], hinweis="keine_belege"
        ),
    )
    antwort = angemeldet.post("/mind", data={"frage": "Wie hoch ist die Vertragsstrafe?"})
    # Erwuenschtes Ergebnis, kein Ausfall: normaler Statuscode, keine Alarmrolle.
    assert antwort.status_code == 200
    text = antwort.text
    assert "Dazu finde ich in den vorliegenden Unterlagen nichts." in text
    assert "role=\"alert\"" not in text
    assert "nicht möglich" not in text
    # Der interne Verwerfungsgrund bleibt im Audit-Log, nicht auf der Seite.
    assert "keine_belege" not in text


@pytest.mark.parametrize(
    "fehler",
    [EngineFehler("Engine antwortet nicht"), UpstreamFehler("Bautics Engine", "HTTP 502")],
)
def test_engine_ausfall_ist_etwas_anderes_als_nichts_gefunden(angemeldet, monkeypatch, fehler):
    def platzt(*_a, **_k):
        raise fehler

    monkeypatch.setattr(mind, "beantworte_frage", platzt)
    antwort = angemeldet.post("/mind", data={"frage": "Wie hoch ist die Vertragsstrafe?"})
    assert antwort.status_code == 503
    text = antwort.text
    assert "Die Auskunft ist derzeit nicht möglich." in text
    assert "Dazu finde ich in den vorliegenden Unterlagen nichts." not in text
    # Kein technischer Rohtext nach aussen.
    assert "HTTP 502" not in text


def test_zu_kurze_frage_wird_hoeflich_abgewiesen(angemeldet, monkeypatch):
    def darf_nicht_laufen(*_a, **_k):  # pragma: no cover - soll nie aufgerufen werden
        raise AssertionError("Die Engine darf bei leerer Frage gar nicht befragt werden.")

    monkeypatch.setattr(mind, "beantworte_frage", darf_nicht_laufen)
    antwort = angemeldet.post("/mind", data={"frage": "ok"})
    assert antwort.status_code == 400
    assert "mindestens drei Zeichen" in antwort.text


# --- Mind: Wissensbank ------------------------------------------------------


def test_wissensbank_zeigt_dokumente_und_abschnitte(angemeldet):
    from tests.conftest import lege_chunks_an

    lege_chunks_an(
        "LV_Los4.pdf", ["Position 03.02.040 Rohrgraben", "Position 03.02.050 Verfüllen"]
    )
    antwort = angemeldet.get("/mind/wissensbank")
    assert antwort.status_code == 200
    text = antwort.text
    assert "Eingelesene Dokumente" in text
    assert "Zitierfähige Abschnitte" in text
    assert "SuedLink Baulos 4" in text
    # Dokumentinhalte gehoeren nicht auf die Statusseite.
    assert "Rohrgraben" not in text


def test_indexlauf_wird_angestossen_und_bilanziert(angemeldet, monkeypatch):
    bericht = mind.IndexBericht(geprueft=3, neu=2, aktualisiert=1, unveraendert=0, chunks=57)
    monkeypatch.setattr(mind, "indexiere_wissensbank", lambda *a, **k: bericht)

    antwort = angemeldet.post("/mind/wissensbank")
    assert antwort.status_code == 303
    assert antwort.headers["location"] == "/mind/wissensbank"

    text = angemeldet.get("/mind/wissensbank").text
    assert "Dateien geprüft" in text
    assert ">3<" in text and ">57<" in text


def test_zweiter_indexlauf_wird_nicht_parallel_gestartet(angemeldet, monkeypatch):
    """Doppelklick auf "Indexlauf starten" darf nicht zwei Laeufe erzeugen -
    sonst schreiben beide dieselben Chunks."""
    laeufe = {"anzahl": 0}

    def zaehle(*_a, **_k):
        laeufe["anzahl"] += 1
        return mind.IndexBericht(geprueft=1, neu=1, chunks=4)

    monkeypatch.setattr(mind, "indexiere_wissensbank", zaehle)
    assert indexlauf.beanspruche() is True      # ein Lauf haelt den Platz
    angemeldet.post("/mind/wissensbank")        # zweiter Anstoss
    assert laeufe["anzahl"] == 0
    indexlauf.zuruecksetzen()


def test_abgebrochener_indexlauf_nennt_keine_technischen_einzelheiten(angemeldet, monkeypatch):
    def platzt(*_a, **_k):
        raise RuntimeError("Verbindung zu 10.0.0.5 verweigert")

    monkeypatch.setattr(mind, "indexiere_wissensbank", platzt)
    angemeldet.post("/mind/wissensbank")
    text = angemeldet.get("/mind/wissensbank").text
    assert "abgebrochen" in text
    assert "10.0.0.5" not in text


# --- Rahmen: Seitenleiste und eiserne Regeln --------------------------------


def test_seitenleiste_zeigt_alle_zehn_agenten_aber_nur_zwei_nutzbar(angemeldet):
    text = angemeldet.get("/echo").text
    alle = ("Echo", "Mind", "Scribe", "Pulse", "Claim", "Track", "Scan", "Radar", "Bid", "Delta")
    for name in alle:
        assert name in text, name
    # Die acht ungebauten Agenten sind sichtbar, aber nicht anklickbar.
    assert text.count('aria-disabled="true"') == 8
    for pfad in ("/scribe", "/pulse", "/claim", "/track", "/scan", "/radar", "/bid", "/delta"):
        assert f'href="{pfad}"' not in text


def test_oberflaeche_laedt_nichts_von_fremden_servern(angemeldet):
    """Kein CDN, kein Google Fonts: Auf der Baustelle ist das Netz schlecht,
    und bei Konzernkunden ist der Fremdaufruf ein Datenschutz-Thema."""
    # Der Namensraum in der eingebetteten SVG-Grafik ist keine Ressource -
    # geprueft wird deshalb gezielt auf ladende Attribute.
    ladend = re.compile(r'(?:src|href)\s*=\s*["\']https?://', re.IGNORECASE)
    for pfad in ("/anmelden", "/echo", "/mind", "/mind/wissensbank"):
        text = angemeldet.get(pfad, follow_redirects=True).text
        assert not ladend.search(text), pfad
        assert "fonts.googleapis.com" not in text
        assert "fonts.gstatic.com" not in text
        assert "cdn." not in text


# Modell- und Anbieternamen duerfen in keiner Oberflaeche stehen (CLAUDE.md).
VERBOTENE_NAMEN = (
    "claude", "sonnet", "opus", "anthropic", "openai", "gpt", "whisper",
    "deepgram", "openrouter", "qwen", "twilio", "gemini", "llama",
)


@pytest.mark.parametrize("pfad", ["/echo", "/mind", "/mind/wissensbank"])
def test_kein_modell_oder_anbietername_in_der_oberflaeche(angemeldet, monkeypatch, pfad):
    lege_bericht_an(sid=f"SM-namen-{pfad.count('/')}", daten=_voller_bericht())
    klein = angemeldet.get(pfad).text.lower()
    for name in VERBOTENE_NAMEN:
        assert name not in klein, f"{name!r} steht in {pfad}"


def test_anmeldeseite_und_detailseite_nennen_keine_modelle(klient):
    bericht_id = lege_bericht_an(sid="SM-namen-detail", daten=_voller_bericht())
    seiten = [klient.get("/anmelden").text]
    klient.post("/anmelden", data={"token": TOKEN})
    seiten.append(klient.get(f"/echo/{bericht_id}").text)
    for seite in seiten:
        for name in VERBOTENE_NAMEN:
            assert name not in seite.lower(), name

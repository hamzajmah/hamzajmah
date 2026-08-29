"""Die Seiten der Produkt-Oberflaeche.

Die Routen rufen dieselbe Fachlogik auf wie die JSON-Schnittstelle
(``mind.beantworte_frage``, ``db``) - kein HTTP-Aufruf gegen den eigenen
Server. Fachlogik von Echo und Mind wird hier nicht angefasst, nur angezeigt.
"""

import hashlib
import logging
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import db, mind, report
from ..http_client import UpstreamFehler
from ..openrouter import EngineFehler
from . import ansicht, indexlauf, sitzung
from .agenten import AGENTEN

logger = logging.getLogger(__name__)

VORLAGEN_VERZEICHNIS = Path(__file__).resolve().parent.parent / "templates"
STATISCH_VERZEICHNIS = Path(__file__).resolve().parent.parent / "static"
STYLESHEET = STATISCH_VERZEICHNIS / "css" / "bautics.css"

# Wie viele Berichte die Liste hoechstens zeigt.
BERICHTE_JE_SEITE = 100

vorlagen = Jinja2Templates(directory=str(VORLAGEN_VERZEICHNIS))
vorlagen.env.filters.update(ansicht.FILTER)
# Sichtbarer Platzhalter statt erfundener Werte - dieselbe Markierung wie im
# gerenderten Berichtstext.
vorlagen.env.globals["leer"] = report.LEER_MARKIERUNG


def _stylesheet_kennung() -> str:
    """Kurzer Hash des Stylesheets fuer die Cache-Abfrage.

    Ohne ihn sehen Baustellen-Tablets nach einem Deploy noch tagelang das
    alte CSS aus dem Browser-Cache.
    """
    try:
        roh = STYLESHEET.read_bytes()
    except OSError:
        return "dev"
    return hashlib.sha256(roh).hexdigest()[:10]


STYLESHEET_KENNUNG = _stylesheet_kennung()

router = APIRouter(include_in_schema=False)


# --- gemeinsamer Seitenrahmen ----------------------------------------------


def _baulose(gewaehlt: Optional[str]) -> tuple[list[str], Optional[str]]:
    """Auswahlliste fuer die Kopfzeile und das tatsaechlich gueltige Baulos.

    Ein Baulos, zu dem es nichts gibt, wird nicht stillschweigend uebernommen -
    sonst zeigt die Kopfzeile ein Projekt an, das gar nicht existiert.
    """
    with db.sitzung() as offen:
        verfuegbar = db.bekannte_baulose(offen)
    if gewaehlt and gewaehlt in verfuegbar:
        return verfuegbar, gewaehlt
    return verfuegbar, None


def _rahmen(
    request: Request,
    *,
    aktiv: str,
    baulos: Optional[str] = None,
    baulose: Optional[list[str]] = None,
) -> dict[str, object]:
    """Kontext, den jede Seite braucht."""
    return {
        "request": request,
        "agenten": AGENTEN,
        "aktiv": aktiv,
        "baulos": baulos,
        "baulose": baulose or [],
        "schutz_aktiv": sitzung.schutz_aktiv(),
        "css_kennung": STYLESHEET_KENNUNG,
    }


def _seite(
    request: Request, vorlage: str, kontext: dict[str, object], statuscode: int = 200
) -> HTMLResponse:
    return vorlagen.TemplateResponse(request, vorlage, kontext, status_code=statuscode)


# --- Anmeldung --------------------------------------------------------------


def _anmelde_kontext(
    request: Request, weiter: str, fehler: Optional[str] = None
) -> dict[str, object]:
    """Kontext des Anmeldeformulars - bewusst ohne Agentenleiste und ohne
    Projektnamen: Wer nicht angemeldet ist, sieht davon nichts."""
    return {
        "request": request,
        "weiter": weiter,
        "fehler": fehler,
        "css_kennung": STYLESHEET_KENNUNG,
    }


def _sicheres_ziel(weiter: Optional[str]) -> str:
    """Nur Pfade auf diesem Server - kein offener Redirect nach aussen."""
    if not weiter or not weiter.startswith("/") or weiter.startswith("//"):
        return "/echo"
    return weiter


@router.get("/")
def startseite(request: Request) -> RedirectResponse:
    ziel = "/echo" if sitzung.ist_angemeldet(request) else "/anmelden"
    return RedirectResponse(ziel, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/anmelden", response_class=HTMLResponse)
def anmeldeformular(request: Request, weiter: Optional[str] = None) -> HTMLResponse:
    if sitzung.ist_angemeldet(request):
        return RedirectResponse(_sicheres_ziel(weiter), status_code=status.HTTP_303_SEE_OTHER)
    return _seite(request, "web/anmelden.html", _anmelde_kontext(request, _sicheres_ziel(weiter)))


@router.post("/anmelden", response_class=HTMLResponse)
def anmelden(
    request: Request,
    token: Annotated[str, Form()] = "",
    weiter: Annotated[str, Form()] = "",
) -> HTMLResponse:
    ziel = _sicheres_ziel(weiter)
    if not sitzung.schutz_aktiv():
        # Ohne konfiguriertes Token gibt es nichts zu pruefen - dann auch
        # kein Formular, das Schutz vortaeuscht.
        return RedirectResponse(ziel, status_code=status.HTTP_303_SEE_OTHER)

    kennung = sitzung.anfrage_kennung(request)
    if not sitzung.darf_versuchen(kennung):
        logger.warning("Anmeldeversuche vorlaeufig gesperrt.")
        return _seite(
            request,
            "web/anmelden.html",
            _anmelde_kontext(
                request, ziel, "Zu viele Fehlversuche. Bitte in einigen Minuten erneut versuchen."
            ),
            statuscode=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    if not sitzung.token_stimmt(token):
        # Das eingegebene Token wird nirgends protokolliert.
        sitzung.vermerke_fehlversuch(kennung)
        logger.warning("Anmeldung mit falschem Token abgewiesen.")
        return _seite(
            request,
            "web/anmelden.html",
            _anmelde_kontext(request, ziel, "Das Zugangstoken stimmt nicht."),
            statuscode=status.HTTP_401_UNAUTHORIZED,
        )

    sitzung.loesche_versuche(kennung)
    antwort = RedirectResponse(ziel, status_code=status.HTTP_303_SEE_OTHER)
    sitzung.setze_cookie(antwort)
    logger.info("Anmeldung an der Oberflaeche erfolgreich.")
    return antwort


@router.post("/abmelden")
def abmelden() -> RedirectResponse:
    antwort = RedirectResponse("/anmelden", status_code=status.HTTP_303_SEE_OTHER)
    sitzung.loesche_cookie(antwort)
    return antwort


# --- Echo: Tagesberichte ----------------------------------------------------


@router.get(
    "/echo",
    response_class=HTMLResponse,
    dependencies=[Depends(sitzung.verlange_anmeldung)],
)
def echo_liste(request: Request, baulos: Optional[str] = None) -> HTMLResponse:
    baulose, gewaehlt = _baulose(baulos)
    with db.sitzung() as offen:
        berichte = db.liste_tagesberichte(offen, projekt=gewaehlt, limit=BERICHTE_JE_SEITE)

    kontext = _rahmen(request, aktiv="Echo", baulos=gewaehlt, baulose=baulose)
    kontext.update(
        {
            "berichte": [
                {
                    "id": bericht.id,
                    "nummer": ansicht.berichtsnummer(bericht),
                    "datum": bericht.datum,
                    "projekt": bericht.projekt,
                    "zustand": ansicht.bericht_zustand(bericht.status),
                }
                for bericht in berichte
            ],
            "grenze": BERICHTE_JE_SEITE,
        }
    )
    return _seite(request, "web/echo_liste.html", kontext)


@router.get(
    "/echo/{bericht_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(sitzung.verlange_anmeldung)],
)
def echo_detail(request: Request, bericht_id: int) -> HTMLResponse:
    with db.sitzung() as offen:
        bericht = db.finde_bericht(offen, bericht_id)
        if bericht is None:
            kontext = _rahmen(request, aktiv="Echo")
            kontext["meldung"] = f"Es gibt keinen Bericht mit der Kennung {bericht_id}."
            return _seite(
                request,
                "web/nicht_gefunden.html",
                kontext,
                statuscode=status.HTTP_404_NOT_FOUND,
            )
        baulose = db.bekannte_baulose(offen)

    kontext = _rahmen(request, aktiv="Echo", baulos=bericht.projekt, baulose=baulose)
    kontext.update(
        {
            "bericht": bericht,
            "nummer": ansicht.berichtsnummer(bericht),
            "zustand": ansicht.bericht_zustand(bericht.status),
            "daten": ansicht.lese_daten(bericht),
            "ereignis_zustand": ansicht.ereignis_zustand,
        }
    )
    return _seite(request, "web/echo_detail.html", kontext)


# --- Mind: Wissensbank ------------------------------------------------------


def _mind_kontext(request: Request, baulos: Optional[str]) -> dict[str, object]:
    baulose, gewaehlt = _baulose(baulos)
    kontext = _rahmen(request, aktiv="Mind", baulos=gewaehlt, baulose=baulose)
    kontext.update({"frage": "", "ergebnis": None, "engine_ausfall": False})
    return kontext


@router.get(
    "/mind",
    response_class=HTMLResponse,
    dependencies=[Depends(sitzung.verlange_anmeldung)],
)
def mind_seite(request: Request, baulos: Optional[str] = None) -> HTMLResponse:
    return _seite(request, "web/mind.html", _mind_kontext(request, baulos))


@router.post(
    "/mind", response_class=HTMLResponse, dependencies=[Depends(sitzung.verlange_anmeldung)]
)
def mind_fragen(
    request: Request,
    frage: Annotated[str, Form()] = "",
    baulos: Annotated[str, Form()] = "",
) -> HTMLResponse:
    # Bewusst POST statt GET: Die Frage ist ein Kundendatum und hat in
    # Zugriffsprotokollen und Browser-Verlaeufen nichts zu suchen.
    kontext = _mind_kontext(request, baulos or None)
    text = frage.strip()
    kontext["frage"] = text
    if len(text) < 3:
        kontext["hinweis"] = "Bitte eine Frage von mindestens drei Zeichen eingeben."
        return _seite(request, "web/mind.html", kontext, statuscode=status.HTTP_400_BAD_REQUEST)

    gewaehltes_baulos = kontext["baulos"]
    try:
        ergebnis = mind.beantworte_frage(
            text, baulos=gewaehltes_baulos if isinstance(gewaehltes_baulos, str) else None
        )
    except (EngineFehler, UpstreamFehler) as fehler:
        # Ein Ausfall ist ausdruecklich kein "dazu finde ich nichts" - das
        # waere eine Aussage ueber die Unterlagen, die niemand geprueft hat.
        logger.warning("Frage konnte nicht beantwortet werden: %s", type(fehler).__name__)
        kontext["engine_ausfall"] = True
        return _seite(
            request, "web/mind.html", kontext, statuscode=status.HTTP_503_SERVICE_UNAVAILABLE
        )

    # Nur gefunden/antwort/fundstellen gehen an die Seite. Der interne
    # Verwerfungsgrund (``hinweis``) bleibt im Audit-Log.
    kontext["ergebnis"] = {
        "gefunden": ergebnis.gefunden,
        "antwort": ergebnis.antwort,
        "fundstellen": ergebnis.fundstellen,
    }
    return _seite(request, "web/mind.html", kontext)


@router.get(
    "/mind/wissensbank",
    response_class=HTMLResponse,
    dependencies=[Depends(sitzung.verlange_anmeldung)],
)
def wissensbank_seite(request: Request) -> HTMLResponse:
    baulose, _ = _baulose(None)
    kontext = _rahmen(request, aktiv="Mind", baulose=baulose)
    kontext.update({"status": mind.wissensbank_status(), "lauf": indexlauf.zustand()})
    return _seite(request, "web/mind_wissensbank.html", kontext)


@router.post(
    "/mind/wissensbank",
    dependencies=[Depends(sitzung.verlange_anmeldung)],
)
def wissensbank_indexieren(hintergrund: BackgroundTasks) -> RedirectResponse:
    if indexlauf.beanspruche():
        hintergrund.add_task(indexlauf.fuehre_aus)
    else:
        logger.info("Indexlauf laeuft bereits - Anstoss ignoriert.")
    return RedirectResponse("/mind/wissensbank", status_code=status.HTTP_303_SEE_OTHER)

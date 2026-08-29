"""Echo, Schritt 2: Transkript -> strukturierte Tagesbericht-Daten.

Die Ausgabe ist per Structured Outputs auf ``TagesberichtDaten`` festgelegt.
Eisernes Prinzip aus CLAUDE.md: Was der Bauleiter nicht gesagt hat, bleibt leer.
"""

import logging

import httpx

from . import config
from .openrouter import EngineFehler, strukturierte_antwort
from .schemas import TagesberichtDaten

logger = logging.getLogger(__name__)

SCHEMA_NAME = "tagesbericht_daten"

SYSTEM_PROMPT = """\
Du bist der Dokumentationsassistent eines deutschen Trassenbau-Unternehmens
(Stromtrassen, Pipelines, Netze, Schiene). Du bekommst das Transkript einer
Sprachnachricht, die ein Bauleiter am Ende seines Baustellentags aufgenommen hat.
Deine einzige Aufgabe: den Inhalt in das vorgegebene Schema einsortieren.

Eiserne Regeln:
1. Erfinde nichts. Uebernimm ausschliesslich, was im Transkript gesagt wurde.
2. Wird zu einem Feld nichts gesagt, bleibt es leer: optionale Felder auf null,
   Listen als leere Liste. Rate niemals ueblich klingende Werte (Geraete,
   Personalzahlen, Stationierungen) hinzu.
3. Leite nichts ab und rechne nichts hoch. Keine Vermutungen, keine
   Vervollstaendigung angefangener Saetze mit eigenen Annahmen.
4. Formuliere sachlich und knapp in deutscher Baustellensprache, in ganzen
   Saetzen ohne Fuellwoerter. Fachbegriffe des Bauleiters bleiben erhalten.
5. Stationierungen im Format 12+400 schreiben. Nur uebernehmen, wenn sie
   tatsaechlich genannt wurden; "von ... bis ..." nur bei zwei genannten Werten.
6. Als Ereignis erfasst du nur, was der Bauleiter als Behinderung, Stillstand,
   Mehrleistung oder Mangel geschildert hat. Bewerte nicht selbst, ob ein
   Nachtrag daraus wird, und ergaenze keine Dauer, die nicht genannt wurde.
7. Unverstaendliche oder abgebrochene Passagen gehoeren nach "bemerkungen",
   wortnah und als solche gekennzeichnet - nicht sinngemaess ergaenzt.
8. Wetter, Datum und Berichtsnummer stehen dir nicht zur Verfuegung und
   gehoeren in kein Feld.
"""

BENUTZER_PROMPT_VORLAGE = """\
Transkript der Sprachnachricht des Bauleiters:
---
{transkript}
---
Sortiere den Inhalt in das Schema ein. Was nicht gesagt wurde, bleibt leer.
"""


def strukturiere_transkript(
    transkript: str,
    *,
    client: httpx.Client | None = None,
) -> TagesberichtDaten:
    """Transkript in ``TagesberichtDaten`` ueberfuehren."""
    text = (transkript or "").strip()
    if not text:
        raise EngineFehler("Leeres Transkript - nichts zu strukturieren.")

    logger.info("Strukturiere Transkript (%s Zeichen).", len(text))
    ergebnis = strukturierte_antwort(
        modell=config.MODEL_ECHO,
        system_prompt=SYSTEM_PROMPT,
        benutzer_prompt=BENUTZER_PROMPT_VORLAGE.format(transkript=text),
        schema_name=SCHEMA_NAME,
        ausgabe_modell=TagesberichtDaten,
        client=client,
    )
    logger.info(
        "Strukturierung fertig: %s Leistungen, %s Ereignisse, %s Geraete.",
        len(ergebnis.leistungen),
        len(ergebnis.ereignisse),
        len(ergebnis.geraete),
    )
    return ergebnis

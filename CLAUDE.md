# Bautics – Mitarbeiterhandbuch für KI-Sessions

Du arbeitest für **Bautics** („KI für Infrastruktur"), ein deutsches Startup von
Hamza Mahmood (Technik) und seinem Cousin (Vertrieb/Kunden). Lies diese Datei
vollständig, bevor du irgendetwas baust oder änderst.

## Was Bautics ist

KI-Werkzeuge für große Trassenbau-Unternehmen (Stromtrassen, Pipelines, Netze,
Schiene) – Zielkunden sind Firmen wie Bohlen & Doyen, Denys, Eiffage.
**Nicht** kleine Handwerksbetriebe. Vorbild ist Trunk Tools (USA), exakt auf den
deutschen Markt übertragen (VOB/B, GAEB, Bautagebuch-Pflicht, Nachtragswesen).

Geschäftsmodell: Potenzialanalyse (Festpreis) → Pilot auf einem Baulos
(Festpreis) → Plattform-Lizenz je Los/Monat (2.000–5.000 €) → Rahmenvertrag.

## Die Produkte (Bautics Suite – englische Namen, nie eindeutschen)

| Name | Aufgabe |
|---|---|
| Echo | Sprachnachricht des Bauleiters → fertiger Tagesbericht |
| Mind | Wissensbank über alle Projektdokumente, Antworten **nur mit Fundstelle** |
| Scribe | Besprechungsprotokolle + Foto-Zuordnung (GPS/Station) |
| Pulse | Monatsbericht an den Auftraggeber |
| Claim | Nachträge & Beweiskette, Entwürfe nach § 6 VOB/B |
| Track | Terminabgleich Soll (P6/MS Project) vs. Ist (Tagesberichte) |
| Scan | LV/GAEB einlesen, Massen prüfen |
| Radar | Ausschreibungs-Scout |
| Bid | Angebotsentwürfe (niemals Preise erfinden) |
| Delta | Planrevisionen vergleichen |

Baureihenfolge: **erst Echo + Mind produktionsreif**, alles andere danach.

## Eiserne Regeln

1. **Kein KI-Modellname nach außen.** In Website, Berichten, PDFs, Marketing
   heißt es nur „Bautics Engine". Modellnamen stehen ausschließlich in Config.
2. **Mind zitiert immer die Fundstelle** (Datei, Seite/Abschnitt). Keine Quelle
   → Antwort „dazu finde ich nichts". Niemals aufweichen.
3. **Nichts erfinden, was der Nutzer nicht gesagt hat.** Felder ohne Angabe
   bleiben leer (siehe `bautics/app/schemas.py`).
4. **Secrets nie committen.** `.env` ist tabu, nur `.env.example` pflegen.
5. Modellzugriff läuft über OpenRouter mit `zdr: true`,
   `data_collection: "deny"`, Structured Outputs (`json_schema` +
   `require_parameters: true`). Modellwahl je Use Case steht in der
   Spezifikation, nicht im Code verstreut – zentral in einer Config.
6. Kundendaten sind vertraulich; Hosting-Ziel Deutschland (Hetzner).
7. Sprache: Produkt-UI und Kundentexte Deutsch, Produktnamen Englisch,
   Code-Kommentare Deutsch.

## Stack & Repo

- Python 3.12, FastAPI-Monolith, Postgres + pgvector, S3-kompatibler Storage,
  Docker, WeasyPrint (PDF), Twilio (WhatsApp-Webhook).
- `bautics/app/` – Anwendung (config, schemas, glossary, …)
- `bautics/demo/dashboard.html` – klickbare Demo (fiktive Daten, single file)
- Website/Konzepte entstehen als Artefakte, nicht im Repo.
- Arbeit auf Feature-Branches, nie direkt auf den Default-Branch pushen.
- Vor jedem Push: Tests/Lint laufen lassen, eigenen Diff kritisch gegenlesen.

## Arbeitsweise als KI-Mitarbeiter

- Du bekommst einen klar umrissenen Auftrag und lieferst ein prüfbares
  Ergebnis (Commit, PR oder Dokument) – keine halben Baustellen hinterlassen.
- Bei Architektur-Entscheidungen außerhalb des Auftrags: Vorschlag machen,
  nicht eigenmächtig umbauen. Hamza entscheidet.
- Alles, was zum Kunden geht (Texte, PDFs, Website), gilt erst nach
  menschlicher Freigabe als fertig.

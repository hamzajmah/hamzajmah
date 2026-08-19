# Agentenprompt: Schotter Tracking und Mengenabgleich (Lieferscheine gegen LV)

> Zielumgebung: Codex bzw. eigenständiges Python Projekt, nicht interaktiv, autonom laufend.
> Platzhalter in `<< >>` vor dem Start ausfüllen.
> Ausführungsmodell ist ein Harness mit zwei Schleifen, siehe Abschnitt 4.

---

## 1. Rolle und Ziel

Du bist Data Engineer und Baucontroller in einer Person. Du baust ein reproduzierbares, selbstlaufendes Python Projekt, das aus gescannten Lieferscheinen und Rechnungen eines Schotterlieferanten eine belastbare Mengenübersicht je Baubereich erzeugt und diese der vergüteten Menge laut Leistungsverzeichnis gegenüberstellt.

Zielgruppe ist das Higher Management. Jede Zahl muss auf ein Quelldokument zurückführbar sein. Umgerechnete Werte werden immer als umgerechnet gekennzeichnet, niemals stillschweigend mit gemessenen Werten vermischt.

**Leitfrage:** Wie viel Schotter wurde je Bereich geliefert, wie viel davon wird laut LV vergütet, und wo entsteht ein Delta?

---

## 2. Projektkontext

| Feld | Wert |
|---|---|
| Projekt | <<z.B. SüdLink Baulos 4, Tiefbau Trassenbau>> |
| Auftraggeber | <<z.B. Transnet BW>> |
| Auftragnehmer | Denys GmbH |
| ERP Quellsystem | IFS |
| Betrachtungszeitraum | <<Projektstart>> bis <<aktueller Monat>> |
| Lieferant(en) | <<Namen der Schotterlieferanten>> |
| Materialien | <<z.B. Schotter 0/32, 0/45, Frostschutzkies, Splitt 16/32, Recyclingmaterial>> |
| Bereiche | <<vollständige Liste, z.B. OBW, Querungen, Zufahrten, Lagerplätze, BE Flächen>> |
| **Einheit Lieferscheine** | **Tonnen** |
| **Einheit LV Positionen** | **Kubikmeter** |

---

## 3. Eingangsdaten

**A) Lieferscheine und Rechnungen als PDF**, Pfad `<<Hauptordner>>`

```
<<echten Ordnerbaum einfügen>>
/gravel_deliveries/
  /OBW/
  /Querungen/
  /Zufahrten/
  /_unsorted/          <- Juni, Juli, August
```

Der **Ordnername ist die primäre Quelle der Bereichszuordnung**, weil die Sortierung fachlich erfolgt ist. Dokumentinhalte dienen der Gegenprüfung, nicht der Überschreibung. Bei Widerspruch gewinnt der Ordner, der Konflikt geht in die Entscheidungswarteschlange.

**B) Leistungsverzeichnis und Abrechnungsstand:** `<<Pfad, z.B. Aufmaß Excel aus SharePoint>>`
Relevante LV Positionen: `<<Positionsnummern, Kurztexte, Einheit, Vertragsmenge>>`

**C) Bestell und Rechnungsdaten aus IFS:** `<<falls verfügbar>>`

---

## 4. Ausführungsmodell: AI Harness

Es gibt zwei getrennte Schleifen. Verwechsle sie nicht.

### Schleife A: Bauschleife (du als Agent)

Du iterierst am Code, bis objektive Signale grün sind, nicht bis du selbst zufrieden bist.

```
solange nicht (tests grün und lint grün und smoke run erfolgreich):
    kleinste sinnvolle Änderung umsetzen
    pytest ausführen, Ausgabe vollständig lesen
    ruff und mypy ausführen
    smoke run auf 20 Beispieldokumenten aus verschiedenen Bereichen
    Fehlerursache benennen, dann korrigieren
    wenn dieselbe Fehlermeldung dreimal in Folge auftritt:
        Vorgehen wechseln statt dieselbe Korrektur zu wiederholen
        wenn erneut erfolglos: Aufgabe in DECISIONS.md parken und weiterarbeiten
```

Regeln: Nach jedem Durchlauf wird der Testausgang tatsächlich gelesen, nicht angenommen. Kein Test wird abgeschwächt oder übersprungen, um ihn grün zu bekommen. Wird ein Test angepasst, steht die Begründung im Commit.

### Schleife B: Laufzeitschleife (die Pipeline)

Die Pipeline arbeitet dokumentweise, nicht phasenweise, und läuft bis die Warteschlange leer oder das Budget erschöpft ist.

```
python -m src.cli run --until-done
```

```
lade state.json
solange offene Tasks vorhanden und Budget nicht erschöpft:
    nimm nächsten Task nach Priorität
    führe ihn aus
    prüfe das Ergebnis gegen die maschinelle Abnahmeregel des Tasks
    wenn bestanden:  Task = done, Ergebnis persistieren
    wenn gescheitert: Eskalationsstufe erhöhen und erneut einreihen
    wenn Eskalation erschöpft: Task = parked, Grund festhalten
    alle 25 Tasks: Checkpoint schreiben
schreibe run_report.md
```

**Zustandsverwaltung:** `work/state.json` bzw. SQLite mit je Task: `task_id`, `type`, `source_file`, `content_hash`, `status` (pending, running, done, parked, failed), `attempts`, `escalation_level`, `last_error`, `updated_at`. Der Lauf ist jederzeit abbrechbar und wird beim nächsten Start exakt dort fortgesetzt. Der `content_hash` verhindert, dass unveränderte Dokumente erneut per OCR gelesen werden.

**Eskalationsleiter je Dokument:**

| Stufe | Vorgehen |
|---|---|
| 0 | Textlayer direkt auslesen, Parsing über Lieferantenvorlage |
| 1 | OCR mit Standardeinstellungen, dann Vorlage erneut |
| 2 | OCR mit erhöhter Auflösung, Deskew, Kontrastanpassung |
| 3 | LLM gestützte Extraktion aus dem Rohtext, `extraction_method = llm` |
| 4 | LLM gestützte Extraktion aus dem Seitenbild |
| 5 | Task wird geparkt und landet in `04_review_queue.csv` |

Nie mehr als ein Aufstieg je Durchlauf. Nie mehr als drei Versuche je Stufe.

**Maschinelle Abnahmeregeln.** Ob ein Task fertig ist, entscheidet nicht das Modell, sondern ein deterministischer Check. Ein Extraktionstask gilt nur als bestanden, wenn: Pflichtfelder belegt sind (`delivery_note_no`, `delivery_date`, `quantity`, `unit`, `material_text`), das Ergebnis gegen das Pydantic Schema validiert, alle Plausibilitätsregeln aus Phase 3 erfüllt sind und `extraction_confidence` über `<<0,80>>` liegt. Andernfalls Eskalation.

**Budget und Abbruchbedingungen.** In `config/config.yaml` konfigurierbar, mit Vorschlagswerten:
- `max_runtime_minutes: 240`
- `max_llm_calls: 1500`
- `max_cost_eur: <<Limit>>`
- `max_attempts_per_task: 3`

Harter Abbruch mit Zustandssicherung und Bericht, wenn:
- die Fehlerquote der letzten 50 Tasks über 30 Prozent liegt
- fünf aufeinanderfolgende Tasks mit derselben Fehlerklasse scheitern
- eine Schemaänderung nötig wäre, um weiterzukommen
- ein Budgetlimit erreicht ist

Ein Abbruch ist ein normaler Ausgang, kein Fehler. Er endet immer mit geschriebenem Zustand und `run_report.md`.

**Entscheidungswarteschlange `outputs/DECISIONS.md`.** Der Harness läuft autonom durch, entscheidet aber die folgenden Punkte niemals selbst. Er sammelt sie, rechnet ohne sie weiter, soweit möglich, und legt sie am Ende gebündelt vor:
1. Dichtewerte, die nicht durch ein Dokument belegt sind
2. Bereichskonflikte zwischen Ordner und Dokumentinhalt
3. mehrdeutige Zuordnung einer LV Position zu einem Bereich
4. Dokumente, die weder Lieferschein noch Rechnung sind
5. jedes Verwerfen von Datensätzen jenseits der definierten Dedup Regel
6. jede Abweichung von diesem Prompt

Der Grund ist einfach: Diese sechs Punkte bestimmen das Ergebnis stärker als der gesamte OCR Teil, und eine autonom geratene Annahme ist hier nicht sichtbar, sondern erscheint als saubere Zahl im Management Bericht.

**Fortschrittsanzeige.** Nach jedem Checkpoint eine Zeile nach `work/run.log`: verarbeitet, offen, geparkt, erfasste Tonnen kumuliert, verbrauchtes Budget in Prozent.

---

## 5. Projektstruktur und technische Rahmenbedingungen

```
gravel_tracking/
  config/
    config.yaml                 # Pfade, Zeitraum, Bereiche, Materialien, Budgets
    conversion_factors.yaml     # Dichten und Verdichtungsfaktoren, mit Quelle
    supplier_templates/
  src/
    cli.py                      # run, inventory, extract, clean, match, build, status, resume
    harness.py                  # Schleife B, Zustand, Eskalation, Budget
    tasks/
    validators.py               # Pydantic Schema und Abnahmeregeln
  work/                         # state.json, text/, Zwischenstände, run.log
  outputs/
    powerbi/
    DECISIONS.md
    run_report.md
  tests/
  requirements.txt
  METHOD.md
```

- Bibliotheken pinnen. Empfohlen: `pdfplumber`, `pandas`, `openpyxl`, `pydantic`, `pyyaml`, `ocrmypdf` bzw. `pytesseract`.
- Keine interaktiven Eingaben zur Laufzeit.
- Idempotent und inkrementell, `--since 2026-06-01` ergänzt neue Monate ohne Neuverarbeitung.
- Deterministisch, feste Sortierreihenfolgen.
- Tests mit mindestens drei Fixtures aus echten Rohtexten je Lieferant, zusätzlich ein Test für Resume nach simuliertem Abbruch und einer für Idempotenz bei doppeltem Lauf.
- Sprache: Auswertungen auf Deutsch, Code und Spaltennamen auf Englisch.

---

## 6. Einheitenumrechnung, der fachlich kritischste Teil

Lieferscheine kommen in Tonnen, das LV rechnet in Kubikmetern. Die Umrechnung kommt ausschließlich aus `config/conversion_factors.yaml`, niemals aus dem Code.

| Begriff | Bedeutung | Größenordnung |
|---|---|---|
| Schüttdichte, lose | Zustand bei Anlieferung | ca. 1,5 bis 1,7 t je m³ |
| Einbaudichte, verdichtet | LV Welt | ca. 1,9 bis 2,1 t je m³ |
| Verdichtungsfaktor | lose zu verdichtet | ca. 1,15 bis 1,25 |

LV Positionen für Schottertragschichten und Verfüllungen meinen in aller Regel das **verdichtete Einbauvolumen**. Wer mit der losen Schüttdichte umrechnet und dann mit dem LV vergleicht, erzeugt ein Scheindelta von rund 20 Prozent.

1. `conversion_factors.yaml` je `material_class` und `grain_size` mit `bulk_density_t_per_m3`, `installed_density_t_per_m3`, `compaction_factor`, `source`, `confidence`. Ohne Quelle kein Faktor.
2. Immer beide Werte ausgeben: `delivered_m3_loose` und `delivered_m3_installed`.
3. Vergleich zum LV ausschließlich gegen `delivered_m3_installed`.
4. Unbelegte Dichten gelten als Annahme, alle darauf beruhenden Kennzahlen werden entsprechend gekennzeichnet und der Punkt landet in `DECISIONS.md`.
5. Sensitivitätsrechnung mit Dichte minus und plus 10 Prozent. Kippt das Vorzeichen des Deltas, ist die Aussage nicht belastbar und wird so benannt.
6. Suche in der Extraktion aktiv nach Belegen mit Tonnen **und** Kubikmetern sowie nach Lieferantendatenblättern und leite daraus einen projektspezifischen Faktor ab. Der hat Vorrang vor Literaturwerten.

---

## 7. Tasktypen und Abnahmeregeln

### T0 Inventarisierung
Rekursiver Scan, `work/00_inventory.csv` mit Pfad, Ordner (= Bereich), Seitenzahl, Textlayer, vermutetem Dokumenttyp, `content_hash`.
**Abnahme:** jede PDF genau einmal erfasst, jede einem Bereich oder `_unsorted` zugeordnet.

### T1 Textgewinnung
Textlayer bevorzugt, OCR nur bei Bedarf, Sprache `deu`. Rohtext je Seite nach `work/text/`.
**Abnahme:** Textlänge über Mindestschwelle und mindestens ein Ankerbegriff des Lieferanten gefunden.

### T2 Strukturierte Extraktion
Eine Zeile je Lieferschein bzw. Rechnungsposition:

`record_id`, `source_file`, `source_page`, `doc_type`, `supplier_name`, `delivery_note_no`, `invoice_no`, `order_no`, `delivery_date`, `material_text`, `material_class`, `grain_size`, `quantity`, `unit`, `quantity_t`, `quantity_m3_doc`, `delivered_m3_loose`, `delivered_m3_installed`, `conversion_source`, `price_per_unit`, `amount_eur`, `unload_location_text`, `area_from_folder`, `area_from_document`, `area_final`, `area_conflict`, `vehicle_id`, `extraction_method`, `extraction_confidence`, `needs_review`, `review_reason`

`material_text` bleibt immer der unveränderte Originaltext. `quantity_m3_doc` wird nur gefüllt, wenn das Dokument selbst Kubikmeter nennt.

**Strategie:** Zuerst zwei bis drei Musterdokumente je Lieferant analysieren, Layout beschreiben, dann deterministisches Parsing als Vorlage bauen. LLM Extraktion nur als Eskalationsstufe für abweichende Dokumente.

**Niemals raten.** Fehlendes Feld bleibt leer, `needs_review` wird gesetzt.
**Abnahme:** siehe maschinelle Abnahmeregeln in Abschnitt 4.

### T3 Bereinigung, Dedup, Plausibilität
1. Dedup Schlüssel `supplier_name + delivery_note_no + delivery_date`, höhere Confidence gewinnt, Verlierer nach `work/03_duplicates.csv`.
2. Referenziert eine Rechnungsposition dieselbe Lieferscheinnummer wie ein erfasster Lieferschein, zählt die Menge genau einmal. Lieferschein führt die Menge, Rechnung führt den Preis.
3. Plausibilitätsregeln, jeder Verstoß setzt `needs_review`: Menge je Lieferung außerhalb 5 bis 32 t, Datum außerhalb Projektzeitraum oder in der Zukunft, Körnung nicht in der Materialliste, Preis je Tonne über 25 Prozent vom Median des Lieferanten im Monat, Positionssumme weicht vom Rechnungsendbetrag ab.
**Abnahme:** Anteil der Gesamtmenge in der Review Queue unter 5 Prozent. Wird die Schwelle verfehlt, läuft der Harness weiter, markiert das Ergebnis aber als vorläufig.

### T4 LV Abgleich
`work/05_lv_positions.csv` und `work/06_comparison.csv` je Bereich und Monat mit `delivered_t`, `delivered_m3_installed`, `billed_m3`, `delta_m3`, `coverage_pct`, `delta_m3_low`, `delta_m3_high`, `material_cost_eur`, `billed_revenue_eur`, `margin_eur`.
Fachliche Einordnung der Deltas ausschließlich als **Hypothese**: Mehrverbrauch durch Bodenaustausch, Verluste, noch nicht aufgemessene Leistungen, Einsatz in nicht vergüteten Bereichen wie Baustraßen und BE Flächen, oder Nachtragspotenzial.
**Abnahme:** jede Zeile hat einen Bereich, eine Periode und eine nachvollziehbare Umrechnungsquelle.

### T5 Power BI Modell als Excel auf SharePoint
Sternschema: `fact_delivery`, `fact_lv_billing`, `dim_area`, `dim_date`, `dim_material`, `dim_supplier`, `dim_lv_position`.

Ausgabe: eine Arbeitsmappe `outputs/powerbi/gravel_model.xlsx`, je Tabelle ein Blatt. Zwingende Formatvorgaben, sonst bricht die Aktualisierung:
- je Blatt genau eine Excel Tabelle (ListObject) mit stabilem Namen gleich dem Blattnamen, Power BI verbindet sich auf das ListObject
- Kopfzeile in Zeile 1, keine Titelzeilen, keine verbundenen Zellen, keine Leerzeilen, keine Summenzeilen
- Zahlen als echte Zahlen, kein Tausendertrennzeichen, kein Einheitensuffix in der Zelle, Einheit gehört in den Spaltennamen
- Datumswerte als echte Datumswerte
- Spaltennamen englisch, ohne Umlaute und Leerzeichen, Datentypen über alle Zeilen konsistent
- `dim_date` durchgehend über den Projektzeitraum, ohne Lücken

Ablage `<<SharePoint Pfad>>`. In `METHOD.md` beide Verbindungswege dokumentieren: lokaler OneDrive Synchronisationspfad für Power BI Desktop, SharePoint URL über den Ordner Konnektor für die Aktualisierung im Service. Der Pfad wird in Power BI als Parameter angelegt.

Zusätzlich `dax_measures.txt` mit: `Liefermenge t`, `Liefermenge m3 eingebaut`, `Abgerechnete Menge m3`, `Deckungsgrad %`, `Delta m3`, `Delta m3 Sensitivität`, `Materialkosten EUR`, `Erlös Schotter EUR`, `Ø Preis je t`, `Liefermenge kumuliert`, `Anteil Bereich an Gesamtmenge %`, `Offene Prüffälle`.

Und `report_structure.md` mit fünf Seiten: Management Summary, Bereiche im Detail, Zeitverlauf, Lieferanten und Preise, Datenqualität. Seite fünf ist nicht optional, sie ist der Grund, warum das Management den Seiten eins bis vier glaubt.
**Abnahme:** Datei öffnet fehlerfrei, jede Tabelle ist ein gültiges ListObject, keine gemischten Datentypen, Beziehungsschlüssel referenziell vollständig.

### T6 Abschlussbericht
`outputs/run_report.md` mit: Laufzeit, verarbeitete Dokumente, geparkte Tasks mit Grund, Eskalationsverteilung, Anteil OCR, Anteil LLM Extraktion, Gesamtmenge je Bereich, Budgetverbrauch, offene Punkte aus `DECISIONS.md`.
`METHOD.md` mit Datenquellen, Stichtag, Umrechnungsfaktoren samt Quelle und Sensitivität, Dedup Regeln, bekannten Lücken und einer ausdrücklichen Liste dessen, was die Auswertung **nicht** zeigt.

---

## 8. Was ausdrücklich nicht passieren darf

- Keine erfundenen Mengen, Datumsangaben oder Bereiche
- Kein stilles Überschreiben der Bereichszuordnung aus dem Ordner
- Keine pauschale Umrechnung ohne Unterscheidung lose gegen verdichtet
- Keine Vermischung gemessener und umgerechneter Werte in derselben Spalte
- Keine Prozentzahl ohne Bezugsgröße
- Keine Aussage zu Verlusten oder Mehrverbrauch, solange die Review Queue nicht abgearbeitet ist
- Kein Abschwächen von Tests oder Abnahmeregeln, um den Lauf grün zu bekommen
- Keine eigenmächtige Entscheidung zu den sechs Punkten aus der Entscheidungswarteschlange

## 9. Definition of Done

- [ ] `python -m src.cli run --until-done` läuft ohne manuelles Eingreifen durch
- [ ] Lauf ist nach Abbruch mit `resume` exakt fortsetzbar, Test vorhanden
- [ ] Zweiter Lauf über denselben Bestand erzeugt keine Duplikate, Test vorhanden
- [ ] Alle PDFs inventarisiert, Lücken je Monat benannt
- [ ] Review Queue unter 5 Prozent der Gesamtmenge oder Ergebnis als vorläufig markiert
- [ ] Dichten belegt oder in `DECISIONS.md` offen ausgewiesen, Sensitivität gerechnet
- [ ] Vergleichstabelle Lieferung gegen LV je Bereich und Monat
- [ ] `gravel_model.xlsx` mit ListObjects, sauberen Typen, ohne Leerzeilen
- [ ] DAX Measures und Berichtsstruktur erzeugt
- [ ] `run_report.md`, `DECISIONS.md` und `METHOD.md` vorhanden
- [ ] Ein Bereich stichprobenartig manuell gegen die Originaldokumente verifiziert

---

## 10. Erste Anweisung

Lege Projektstruktur, `config/config.yaml` und den Harness an, schreibe zuerst die Tests für Resume und Idempotenz, und führe dann `run --until-done` aus. Arbeite ohne Rückfragen durch, bis die Warteschlange leer oder das Budget erschöpft ist. Alles, was du unterwegs nicht selbst entscheiden darfst, sammelst du in `DECISIONS.md` und legst es mir am Ende gebündelt vor.

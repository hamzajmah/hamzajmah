# Power BI: von der Arbeitsmappe zum Bericht

Ziel: ein Bericht, der Liefermenge und abgerechnete Menge im selben Visual
zeigt, sich taeglich selbst aktualisiert und dessen Zahlen bis auf den einzelnen
Wareneingang zurueckverfolgbar sind.

Die Pipeline erzeugt dafuer `outputs/powerbi/gravel_model.xlsx` als fertiges
Sternschema. Power BI modelliert nichts nach, es verbindet nur.

---

## 1. Warum Excel als Zwischenschicht und nicht direkt IFS

Power BI koennte theoretisch direkt auf IFS zugreifen. Dagegen sprechen drei
Dinge: der Wareneingang traegt Zuschlagszeilen mit Tonnen, die niemals
Liefermenge sind; die Umrechnung Tonnen in Kubikmeter braucht dokumentierte
Faktoren; und die Zuordnung LV Position zu Material ist eine fachliche
Entscheidung. Wuerde das in DAX passieren, stuende die Fachlogik in Measures,
die niemand prueft. So steht sie in Python mit Tests, und Power BI zeigt nur an.

---

## 2. Ablage und Pfad

1. `gravel_model.xlsx` in die SharePoint Bibliothek des Projekts legen, in einen
   eigenen Ordner, z.B. `.../Freigegebene Dokumente/Controlling/Schotter/`.
2. In Power BI Desktop einen Parameter `DataFolder` (Text) anlegen.
   - lokal: der OneDrive Synchronisationspfad, z.B.
     `C:\Users\<Benutzer>\<Organisation>\<Projektbibliothek>\Controlling\Schotter`
   - im Service: die SharePoint Bibliotheks URL
3. Jede Abfrage referenziert `DataFolder`, nie einen festen Pfad. Beim Wechsel
   der Umgebung wird genau ein Parameter geaendert.

Fuer die geplante Aktualisierung im Service den **SharePoint Ordner** Konnektor
verwenden, nicht "Excel Arbeitsmappe" mit lokalem Pfad. Nur so laeuft die
Aktualisierung ohne Gateway.

---

## 3. Import

Beim Verbinden erscheinen die Blaetter **und** die Tabellen. Immer die
**Tabelle** (ListObject) waehlen, nie das Blatt: das Blatt liefert leere
Randspalten und kippt Datentypen, sobald eine Zeile dazukommt.

Zu importieren:

| Tabelle | Rolle | Zeilen im aktuellen Stand |
|---|---|---|
| `fact_delivery` | Lieferungen, eine Zeile je Wareneingangsposition | rund 23.000 |
| `fact_lv_billing` | abgerechnete Menge je LV Position und Monat | rund 250 |
| `dim_area` | Bereiche (Abschnitte, Querungen, Sammelbereiche) | rund 60 |
| `dim_date` | Kalender ueber den gesamten Zeitraum, lueckenlos | taeglich |
| `dim_material` | Material und Koernung mit Dichten und Quelle | 8 |
| `dim_material_group` | Bruecke zwischen Material und LV Gruppe | 4 |
| `dim_supplier` | Lieferanten | 1 |
| `dim_lv_position` | LV Positionen mit Kurztext, Kapitelpfad, Vertragsmenge | rund 2.900 |

In Power Query nichts umbauen. Nur pruefen, dass `delivery_date`,
`billing_date` und `date` als Datum erkannt sind und alle Mengenspalten als
Dezimalzahl.

---

## 4. Beziehungen

```
dim_date ──< fact_delivery >── dim_area
   │             │  │
   │             │  └── dim_material ── (dim_material_group)
   │             └───── dim_supplier
   │
   └──────────< fact_lv_billing >── dim_area
                     │
                     ├── dim_material_group
                     └── dim_lv_position
```

Konkret anzulegen:

| von | nach | Typ |
|---|---|---|
| `fact_delivery[delivery_date]` | `dim_date[date]` | n:1, einfach |
| `fact_delivery[area_key]` | `dim_area[area_key]` | n:1, einfach |
| `fact_delivery[material_key]` | `dim_material[material_key]` | n:1, einfach |
| `fact_delivery[material_group]` | `dim_material_group[material_group]` | n:1, einfach |
| `fact_delivery[supplier_key]` | `dim_supplier[supplier_key]` | n:1, einfach |
| `fact_lv_billing[billing_date]` | `dim_date[date]` | n:1, einfach |
| `fact_lv_billing[area_key]` | `dim_area[area_key]` | n:1, einfach |
| `fact_lv_billing[material_group]` | `dim_material_group[material_group]` | n:1, einfach |
| `fact_lv_billing[lv_position_no]` | `dim_lv_position[lv_position_no]` | n:1, einfach |

`dim_date` als Datumstabelle markieren (Spalte `date`). Beide Faktentabellen
haengen an denselben drei Dimensionen `dim_date`, `dim_area` und
`dim_material_group` - genau dadurch stehen Lieferung und Abrechnung im selben
Visual nebeneinander, ohne dass irgendwo eine Formel beide Tabellen verbindet.

Bidirektionale Filter nicht aktivieren. Sie erzeugen hier stille Doppelzaehlung.

---

## 5. Measures

Alle Measures stehen fertig in `outputs/powerbi/dax_measures.txt`. Sie in eine
leere Tabelle `_Measures` einfuegen. Die wichtigsten:

```dax
Liefermenge t =
CALCULATE ( SUM ( fact_delivery[quantity_t] ), fact_delivery[charge_type] = "material_supply" )

Liefermenge m3 eingebaut =
CALCULATE ( SUM ( fact_delivery[delivered_m3_installed] ), fact_delivery[charge_type] = "material_supply" )

Abgerechnete Menge m3 = SUM ( fact_lv_billing[billed_quantity] )

Delta m3 = [Abgerechnete Menge m3] - [Liefermenge m3 eingebaut]

Deckungsgrad % = DIVIDE ( [Abgerechnete Menge m3], [Liefermenge m3 eingebaut] )
```

Der Filter `charge_type = "material_supply"` ist nicht optional. Ohne ihn zaehlt
der Bericht Diesel- und Samstagszuschlaege als Liefermenge mit und weist rund
175.000 Tonnen zu viel aus.

---

## 6. Berichtsseiten

Aufbau in `outputs/powerbi/report_structure.md`, fuenf Seiten:
Management Summary, Bereiche im Detail, Zeitverlauf, Lieferanten und Preise,
Datenqualitaet.

Zwei Dinge gehoeren auf jede Seite mit Deltas:

- ein **Datumsfilter auf den gemeinsamen Zeitraum**. Die Leistungsmeldung rechnet
  ab Sommer 2025 ab, die Lieferdaten beginnen im Januar 2026. Ohne Filter zeigt
  der Bericht ein Delta, das nur aus dem fehlenden Datenbestand stammt.
- ein **Hinweis auf die Dichteannahme**. Die Sensitivitaetsmeasures liefern die
  Bandbreite; sie gehoert als Fehlerbalken oder als zweite Zahl neben jedes Delta.

Fuer die Rueckverfolgbarkeit auf Seite 2 ein Drillthrough auf Belegebene
einrichten: `record_id`, `delivery_note_no`, `source_file`, `source_row_ref`.
Damit landet jede Zahl in zwei Klicks beim einzelnen Wareneingang in IFS.

---

## 7. Aktualisierung

Ablauf im Betrieb:

1. Neuer IFS Export wird in `data/erp/` abgelegt.
2. `python -m src.cli run --until-done` laeuft (lokal oder als geplanter Task).
   Unveraenderte Dateien werden ueber den `content_hash` nicht erneut gelesen.
3. Die Pipeline schreibt `gravel_model.xlsx` in den SharePoint Ordner.
4. Power BI Service aktualisiert nach Zeitplan, z.B. taeglich morgens.

Der Bericht wird also nie in Power BI "repariert". Aendert sich etwas fachlich,
aendert sich die Konfiguration im Projekt (`conversion_factors.yaml`,
`lv_mapping.yaml`) und der naechste Lauf traegt es in den Bericht.

---

## 8. Haeufige Stolpersteine

| Symptom | Ursache | Loesung |
|---|---|---|
| Menge unerklaerlich hoch | Zuschlagszeilen mitgezaehlt | Measure mit `charge_type` Filter verwenden |
| Aktualisierung im Service schlaegt fehl | lokaler Pfad statt SharePoint Konnektor | `DataFolder` auf die Bibliotheks URL umstellen |
| Datumsachse mit Luecken | Datum aus der Faktentabelle statt `dim_date` | Achse aus `dim_date[date]` ziehen |
| Delta springt beim Filtern auf einen Bereich | Schottertragschicht wird im LV projektweit gefuehrt | Vergleich dieser Gruppe nur auf Projektebene, siehe DECISIONS.md |
| Werte doppelt | bidirektionale Beziehung | auf einfache Filterrichtung zuruecksetzen |

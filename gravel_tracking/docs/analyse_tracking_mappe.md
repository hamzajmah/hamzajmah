# Analyse der bestehenden Tracking Mappe

Geprueft: `Schotter_Tracking_Final_20260818.xlsx` und `Schotter_Tracking_PBI_20260818.xlsx`.
Stand der Pruefung: 21.08.2026.

---

## 1. Was die Mappe richtig macht

Die Mappe ist kein Rohentwurf. Vier Dinge darin sind belastbar und wurden
uebernommen:

**Beide Bestellungen sind erfasst.** P100042563 mit 10.290 Zeilen und
244.790 t, P100012091 mit 6.437 Zeilen und 165.041 t. Die Kontrollsumme
409.831,115 t stimmt mit meiner Nachrechnung auf die Stelle. Fuer P100012091
gibt es keinen eigenen Export; ohne die Mappe fehlte der gesamte Zeitraum vor
2026.

**Die Ortszuordnung ist aus Originalbelegen erarbeitet.** 270.084 t haengen an
einer ERP Belegreferenz, weitere 86.538 t am Notizfeld, der Rest an sieben
dokumentierten Abgleichsverfahren mit Konfidenzangabe. Das ist der Teil, den
kein Automatismus aus den Rohdaten herstellen kann, und er loest genau die
Spannen auf, an denen meine Auswertung bisher haengen blieb.

**Die fachliche Verwendung stimmt.** 0/8 und 0/22 Bettung, 0/45 und 50/200
Schotter, 0/2 ausschliesslich Muffengruben, 50/200 auch fuer HDD Plattformen.

**Die Struktur der PBI Mappe ist sauber.** Sternschema, echte Tabellen
(ListObjects), englische Spaltennamen, ein Parameterblatt fuer eine
What-if-Schaltflaeche.

---

## 2. Die vier Luecken

### 2.1 Ein pauschaler Umrechnungsfaktor von 0,4 m3 je Tonne

Alle 16.727 Zeilen tragen denselben Faktor. 0,4 m3 je t entspricht einer Dichte
von 2,5 t je m3. Das ist ungefaehr die Rohdichte des Gesteins, nicht die Dichte
eines eingebauten Gemisches. Uebliche Werte liegen bei 1,9 bis 2,1 t je m3
eingebaut und 1,5 bis 1,7 t je m3 lose.

Die Folge ist kein Schoenheitsfehler, sondern eine Aussage, die kippt:

| | mit Faktor 0,4 | mit materialspezifischen Dichten |
|---|---:|---:|
| Bettungsmaterial geliefert | 69.409 m3 | **86.757 m3** |
| Bettungsmaterial abgerechnet (LV) | 87.482 m3 | 87.482 m3 |
| Deckungsgrad | **126 Prozent** | **100,8 Prozent** |

Mit dem pauschalen Faktor waere mehr abgerechnet als geliefert - beim
Bettungsmaterial physikalisch kaum erklaerbar. Mit materialspezifischen Dichten
trifft die Lieferseite die Abrechnung auf 0,8 Prozent genau. Das ist zugleich
die beste Bestaetigung dafuer, dass Datenbestand und Materialzuordnung stimmen.

Der Faktor gehoert je Material getrennt nach lose und eingebaut hinterlegt, mit
Quelle und Sensitivitaet. Die What-if-Schaltflaeche der PBI Mappe bleibt
sinnvoll, aber als Abweichung von einem belegten Wert, nicht als einziger Wert.

### 2.2 Die LV Seite deckt nur ein Drittel ab

Die Mappe fuehrt 11 LV Positionen, alle vom Typ Schottertragschicht, und
22 Monatszeilen. Im Leistungsverzeichnis sind aber 110 Positionen schotter-
relevant:

| Gruppe | Positionen | abgerechnet m3 |
|---|---:|---:|
| Schottertragschicht | 10 | 110.286 |
| Bettungsmaterial (Leitungszone, Muffengruben) | 44 | 90.782 |
| Arbeitsplattform Querung, pauschal | 56 | keine Menge, PSCH |

Dadurch stellt die Mappe **die gesamte Liefermenge** (409.831 t, auch die
172.970 t Bettungsmaterial) **einer LV Seite gegenueber, die nur die
Schottertragschicht kennt**. Das Kennzahlenfeld "Geliefert gegen Aufmass" im
Management Blatt vergleicht damit zwei verschiedene Dinge.

Richtig getrennt ergibt sich:

| Gruppe | geliefert m3 eingebaut | abgerechnet m3 | Delta | Deckung |
|---|---:|---:|---:|---:|
| Bettungsmaterial | 86.757 | 87.482 | +725 | 100,8 Prozent |
| Schottertragschicht | 120.153 | 110.286 | -9.867 | 91,8 Prozent |

### 2.3 Doppelt gebuchte Lieferscheine sind noch enthalten

41 Materialzeilen mit 682,8 t tragen dieselbe Lieferscheinnummer, dasselbe
Datum, dieselbe Menge und dasselbe Material wie eine andere Zeile, stammen aber
aus zwei verschiedenen Wareneingangsbelegen mit unterschiedlichem Erfasser.
Insgesamt sind es 135 Zeilen einschliesslich Zuschlags- und Annahmezeilen.

Fuer die Menge ist das ein Rundungsthema, fuer die Rechnungspruefung nicht: die
Buchung steht zweimal im ERP.

### 2.4 Fuer P100012091 fehlen Zuschlaege und Annahmemengen

Die Mappe fuehrt fuer diese Bestellung nur Liefermengen. Aus P100042563 ist
bekannt, dass daneben 174.653 t auf Zuschlagszeilen und 130.912 t auf
Annahmezeilen stehen. Fuer 2024 und 2025 fehlt beides. Solange die Kostenseite
mitlaufen soll, ist das eine Luecke.

Kleiner Nebenbefund: 53 Zeilen der Bestellung P100012091 tragen mehr als 32 t,
bis zu 5.951 t in einer Zeile. Das sind Sammelbuchungen mehrerer Fuhren. Sie
sind nicht falsch, aber sie sind auch keine einzelne Lieferung an einem Ort -
fuer eine Ortsauswertung auf Tagesebene ist das relevant.

---

## 3. Was daraus geworden ist

Beide Mappen sind jetzt Quellen der Pipeline, nicht Konkurrenz zu ihr.

- Wareneingaenge der Bestellung P100012091 werden aus der Mappe uebernommen,
  weil kein Export existiert: 6.437 Saetze, 165.041 t.
- Die Ortszuordnung der Mappe veredelt 6.519 Saetze des ERP Exports, dort wo
  das Notizfeld nur eine Spanne oder gar nichts hergibt.
- 112 Faelle, in denen beide Quellen einen belegten, aber verschiedenen Ort
  nennen, wurden **nicht** ueberschrieben. Sie stehen in DECISIONS.md.

Damit steht:

| Kennzahl | vorher (nur ERP Export) | jetzt |
|---|---:|---:|
| Liefermenge | 244.108 t | **409.148 t** |
| Zeitraum | Jan bis Aug 2026 | **Nov 2024 bis Aug 2026** |
| Ortsabdeckung | 73,8 Prozent | **98,9 Prozent** |
| davon nur als Spanne | 36,3 Prozent | **0 Prozent** |
| Umrechnung | materialspezifisch | materialspezifisch |
| LV Positionen im Abgleich | 55 | **110** |

---

## 4. Was fuer Power BI noch fehlt

Die PBI Mappe ist als Struktur richtig, aber inhaltlich zu schmal. Konkret
fehlen ihr gegenueber dem erzeugten Modell:

| fehlt | wofuer |
|---|---|
| `dim_area` mit Sektion und Kilometrierung | Abschnitte, Querungen und Muffengruben als eigene Hierarchie |
| `dim_material_group` | Bruecke zwischen Material und LV Position, sonst kein Vergleich |
| `fact_location_allocation` | Menge je Setzpunkt, getrennt nach belegt und verteilt |
| `dim_structure` | Kilometrierung und Bauweise je Bauwerksflaeche |
| `fact_delivery_log` | Zweitquelle zur Kontrolle |
| Spalten fuer lose und eingebaute Kubikmeter samt Sensitivitaet | jede Aussage mit Bandbreite statt Scheingenauigkeit |
| vollstaendige `fact_lv_billing` | 164 statt 22 Monatszeilen |

Das erzeugte `gravel_model.xlsx` enthaelt all das bereits, in derselben Form
(je Blatt eine Tabelle, stabile Namen, englische Spalten). Der Weg in Power BI
steht in `docs/powerbi_anleitung.md`.

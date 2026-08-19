# Fahrplan: vom Mengengeruest zum Deep Dive Dashboard

Stand 19.08.2026. Ziel ist nicht das Delta, sondern die Frage, **wo wie viel
Material angekommen ist** - belastbar, bis auf den einzelnen Wareneingang
zurueckverfolgbar, und im Dashboard bis auf den Setzpunkt aufloesbar.

---

## 1. Was jetzt steht

| Baustein | Stand |
|---|---|
| IFS Wareneingang, Bestellung P100042563 | 23.370 Zeilen, 244.108 t Schotter, zeilenscharf klassifiziert |
| Positionsarten getrennt | Lieferung, Annahme, Zuschlag, Fracht |
| Bereiche | 21 Abschnitte, 21 Querungen, Muffengruben, Sammelbereich |
| Ortsangaben | 73,8 Prozent der Menge verortet, Punkt und Spanne getrennt gefuehrt |
| Trassenstammdaten | 73 Bauwerksflaechen mit Sektion, Kilometrierung, Bauweise |
| Leistungsmeldung | 2.882 LV Positionen, davon 55 schotterrelevant |
| Lieferlog des Lieferanten | 8.946 Zeilen als Gegenprobe, nicht in der Menge |
| Power BI Modell | 11 Tabellen, Sternschema mit ListObjects |

---

## 2. Was die Gegenprobe ergeben hat

Die Frage war: stimmen die Tonnen? Drei Antworten, in dieser Reihenfolge.

**Der ERP Bestand ist korrekt wiedergegeben.** Die Stichprobe rechnet zwei
Bereiche unabhaengig von der Pipeline nach und kommt zeilen- und tonnengenau auf
dasselbe Ergebnis. Was im Export steht, steht auch im Bericht.

**Der ERP Bestand ist aber nicht der ganze Bau.** Das Lieferlog weist
**191.088 t in elf Monaten aus, in denen der ERP Export leer ist** (Oktober 2024
bis Dezember 2025). Es nennt ausserdem mehrere Lieferwerke, waehrend der
ausgewertete Export genau eine Bestellung abdeckt. Die Aussage
"244.108 t geliefert" gilt fuer P100042563 ab Januar 2026, nicht fuer das
Bauvorhaben.

**Das Lieferdatum im ERP ist teilweise ein Buchungsdatum.** 3.317 Fuhren
(32,4 Prozent der Menge) tragen als Lieferdatum den Ersten eines Monats,
darunter zwei Sonntage mit 622 und 308 Fuhren. Monatssummen sind belastbar,
Tagesverlaeufe nicht.

Im Ueberlappungszeitraum Januar und Februar 2026 liegen beide Quellen
mengenmaessig 4,7 Prozent auseinander (18.686 t gegen 17.816 t), was sich mit
dem Buchungsversatz deckt.

---

## 3. Die entscheidende Entdeckung fuer den Deep Dive

Das Lieferlog notiert den Ort **punktscharf**, wo das ERP nur eine Spanne kennt.
Dieselben Mengen, zwei Genauigkeiten:

| Lieferlog | ERP | Menge |
|---|---|---|
| SP47 | SP034-SP047 | 4.699 t, identisch in beiden Quellen |
| SP164 | SP162-SP167 | 2.267 t |
| SP108 | SP091-SP108 | 2.045 t |

Damit ist der Weg klar: **die Spannen im ERP lassen sich ueber das Lieferlog
aufloesen.** Das betrifft 88.624 t, also 36 Prozent der Liefermenge, die heute
nur als Spanne dastehen.

---

## 4. Fahrplan

### Phase 1 - Ortsaufloesung ueber das Lieferlog (groesster Hebel)

Ziel: aus "SP122-SP131" wird "SP124: 2.100 t, SP127: 3.400 t, ...".

1. Lieferlog als zweite Quelle in die Pipeline aufnehmen, mit eigenem
   `source_system`, nicht in die Menge gemischt.
2. Zuordnung Lieferlogzeile zu ERP Zeile in dieser Reihenfolge:
   Lieferscheinnummer, sonst Datum plus Menge plus Material, sonst
   Datum plus Material. Jede Zuordnung traegt ihre Methode und eine Guete.
3. Wo eine Zuordnung eindeutig ist, uebernimmt der ERP Satz den Ort **und** das
   echte Lieferdatum aus dem Lieferschein. Beides wird als abgeleitet markiert,
   der ERP Wert bleibt in einer eigenen Spalte stehen.
4. Ergebnis: `location_source` = erp_note, supplier_log oder none. Im Dashboard
   filterbar, damit jederzeit sichtbar ist, worauf eine Zahl beruht.

Erwartetes Ergebnis: Ortsabdeckung von 73,8 Prozent auf ueber 90 Prozent,
Spannenanteil deutlich unter 36 Prozent, echte Tagesverlaeufe.

### Phase 2 - Vollstaendigkeit des Bestands

1. Weitere IFS Wareneingangsexporte anfordern: alle Bestellungen mit
   Schotterbezug ab Baubeginn, nicht nur P100042563.
2. Bis dahin das Lieferlog fuer den Zeitraum vor Januar 2026 als
   **vorlaeufige** Mengenquelle fuehren, sichtbar getrennt vom ERP.
3. Die Pipeline kann beides schon: der Bestand waechst ueber die
   Inventarisierung, unveraenderte Dateien werden nie neu gelesen.

### Phase 3 - Ortsstammdaten und Karte

1. Liste der Setzpunkte mit Kilometrierung oder Koordinaten anfordern. Das ist
   der fehlende Schluessel: die Bauwerksflaechen tragen Kilometer, die
   Setzpunkte bisher nicht.
2. Damit entsteht `dim_location` mit Kilometer und Sektion je Setzpunkt.
3. Erst dann ist eine Karte oder ein Trassenband sinnvoll: Menge je Kilometer,
   eingefaerbt nach Material, mit den Querungsbauwerken als Marker.

### Phase 4 - Dashboard

Fuenf Seiten, jede mit einer Frage als Titel.

1. **Wie viel ist angekommen** - Menge nach Material und Monat, kumuliert,
   Anteil je Bereichsgruppe.
2. **Wo ist es angekommen** - Trassenband ueber die Kilometrierung, Balken je
   Setzpunkt, Umschalter zwischen belegter und verteilter Menge, Drillthrough
   bis auf den einzelnen Wareneingang.
3. **Was ist wo verbaut worden** - Lieferung gegen abgerechnete Menge je
   Bereich, mit Sensitivitaetsband statt einer Scheingenauigkeit.
4. **Wer liefert was** - Werke, Transportarten, Zuschlagsarten getrennt von der
   Liefermenge.
5. **Wie gut sind die Daten** - Ortsabdeckung, Anteil Sammelbuchungen, Anteil
   angenommener Dichten, offene Prueffaelle. Diese Seite ist der Grund, warum
   das Management den ersten vier glaubt.

### Phase 5 - Betrieb

1. Neuer Export in `data/erp/`, ein Lauf, fertige Mappe auf SharePoint.
2. Drei Regeln im Wareneingang, die den groessten Teil der offenen Punkte
   erledigen: Lieferscheinnummer erfassen, genau einen Ort je Fuhre notieren,
   echtes Lieferdatum buchen.
3. Monatlicher Blick auf die Datenqualitaetsseite statt auf Nachtraege im
   Nachhinein.

---

## 5. Was ich dafuer brauche

| Nr. | Was | Wofuer | Ohne das |
|---|---|---|---|
| 1 | Setzpunktliste mit Kilometrierung oder Koordinaten | Karte, Trassenband, Zuordnung SP zu Abschnitt | keine raeumliche Darstellung, nur Listen |
| 2 | Weitere IFS Exporte ab Baubeginn | vollstaendige Menge | Auswertung bleibt auf eine Bestellung ab 2026 begrenzt |
| 3 | Freigabe: Spannen gleichmaessig verteilen, ja oder nein | Punktdarstellung im Dashboard | Spannen bleiben als Block stehen |
| 4 | Entscheidung Querungen: Plattform pauschal oder Tragschicht | Deckungsgrad der Gruppe Schotter | Delta bleibt zwischen 70 und 129 Prozent unbestimmt |
| 5 | Ein Dichtebeleg | aus Annahme wird Messung | jede Kubikmeterangabe bleibt eine Annahme |
| 6 | Aktuelles Lieferlog | Ortsaufloesung bis heute | Aufloesung endet im Februar 2026 |

---

## 6. Reihenfolge

Phase 1 zuerst. Sie hebt die Ortsgenauigkeit von 74 auf ueber 90 Prozent und
braucht nichts als die beiden Dateien, die schon vorliegen. Phase 3 braucht die
Setzpunktliste und ist der Schritt, der aus Tabellen ein Bild macht.

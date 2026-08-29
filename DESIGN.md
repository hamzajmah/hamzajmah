# Bautics – Designsystem

Verbindlich für alles, was ein Mensch zu sehen bekommt: Produkt-UI, Website,
PDF-Berichte, Angebote, Präsentationen. Wer eine Oberfläche baut, nimmt diese
Werte, statt neue zu erfinden.

## Farben

Warme, papierartige Grundfläche statt sterilem Weiß – die Anmutung soll an
Bauzeichnung und Papierakte erinnern, nicht an eine beliebige SaaS-Oberfläche.
Das Orange ist Signalfarbe von der Baustelle.

| Rolle | Wert | Verwendung |
|---|---|---|
| Signalorange (Marke) | `#D14E0A` | Akzent, aktive Zustände, Logo. Sparsam – die Wirkung lebt von Seltenheit |
| Signalorange, weich | `#F8E4D6` | Hinterlegung von Akzentflächen |
| Papier | `#F4F1EB` | Grundfläche hell |
| Karte | `#FFFFFF` | Erhöhte Flächen auf Papier |
| Tinte | `#1B1914` | Text |
| Tinte, gedämpft | `#6C675C` | Sekundärtext, Beschriftungen |
| Haarlinie | `#E2DCCF` | Trennlinien |
| Haarlinie, kräftig | `#CFC8B8` | Betonte Trennlinien, Summenstriche |
| Nacht | `#171511` | Dunkle Flächen: Sidebar, Kontrastbänder |
| Nacht, erhöht | `#221F19` | Karten auf dunklem Grund |
| Nacht-Tinte | `#F1EDE4` | Text auf dunklem Grund |
| Nacht, gedämpft | `#9C9585` | Sekundärtext auf dunklem Grund |
| Nacht-Haarlinie | `#35312A` | Trennlinien auf dunklem Grund |

### Zustandsfarben – reserviert

Nur für Zustände, **niemals dekorativ** und nie als Akzentfarbe zweckentfremdet.
Ein Zustand wird zusätzlich benannt oder mit Symbol gekennzeichnet, nie allein
über Farbe (Barrierefreiheit, Ausdrucke in Graustufen).

| Zustand | Farbe | Weich |
|---|---|---|
| Freigegeben, erledigt | `#2E7048` | `#DDEBE2` |
| Prüfen, offen | `#9A6B00` | `#F6EBD2` |
| Kritisch | `#B3261E` | – |

## Schrift

| Rolle | Schrift | Hinweis |
|---|---|---|
| Überschriften | Fraunces | Serifenschrift mit Charakter, sparsam in Gewicht und Größe |
| Fließtext, UI | IBM Plex Sans | Arbeitsschrift |
| Zahlen, Stationierungen, Positionsnummern | IBM Plex Mono | Immer `font-variant-numeric: tabular-nums`, damit Ziffern in Spalten fluchten |

Alle drei über Google Fonts verfügbar. Immer eine echte Ersatzschriftfamilie
angeben (`system-ui, sans-serif` bzw. `serif` / `monospace`).

## Haltung

- **Sparsam mit dem Akzent.** Eine orange Fläche pro Bildschirm wirkt; fünf
  wirken wie ein Baumarktprospekt.
- **Fachsprache ernst nehmen.** Stationierungen als `12+400`, Positionsnummern
  als `03.02.040` – im Monospace-Schnitt, damit sie als Kennung lesbar sind.
- **Zustand vor Dekoration.** Was Aufmerksamkeit braucht, wird durch Form und
  Farbe gemeinsam kenntlich gemacht.
- **Kein Modellname, kein Anbietername** in irgendeiner Oberfläche – nur
  „Bautics Engine" (siehe `CLAUDE.md`).
- Demo-Inhalte immer sichtbar als fiktiv kennzeichnen.

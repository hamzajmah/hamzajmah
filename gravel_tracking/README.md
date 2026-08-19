# Schotter Tracking und Mengenabgleich

Reproduzierbare Pipeline, die aus Lieferbelegen eines Schotterlieferanten eine
belastbare Mengenuebersicht je Baubereich erzeugt und sie der vergueteten Menge
laut Leistungsverzeichnis gegenueberstellt. Umgesetzt nach
`docs/auftragsprompt.md`.

Jede Zahl ist auf ein Quelldokument zurueckfuehrbar. Umgerechnete Werte sind als
umgerechnet gekennzeichnet und werden nie stillschweigend mit gemessenen Werten
vermischt.

## Schnellstart

```bash
pip install -r requirements.txt
python -m src.cli run --until-done     # laeuft ohne Rueckfragen durch
python -m src.cli status               # Zustand der Warteschlange
python -m src.cli resume                # Fortsetzung nach Abbruch
python -m src.cli run --since 2026-06-01  # inkrementell ab Datum
python tools/verify_area.py AS04S-3-04    # Stichprobe gegen die Quelldatei
```

Pruefung:

```bash
python -m pytest -q      # Tests, inklusive Resume und Idempotenz
python -m ruff check .
python -m mypy src
```

## Aufbau

```
config/     config.yaml, conversion_factors.yaml, supplier_templates/
src/        cli.py, harness.py (Laufzeitschleife), tasks/, validators.py
work/       state.json, Zwischenstaende, run.log     (nicht versioniert)
outputs/    powerbi/, DECISIONS.md, run_report.md    (nicht versioniert)
tests/      Fixtures und Tests
tools/      unabhaengige Nachrechnung einzelner Bereiche
```

## Wie die Pipeline laeuft

Die Laufzeitschleife arbeitet dokumentweise. Jeder Task hat eine maschinelle
Abnahmeregel; scheitert er, steigt er eine Eskalationsstufe hoch (Textlayer,
OCR, OCR mit hoeherer Aufloesung, LLM, geparkt). Der Zustand liegt in
`work/state.json`, ein Abbruch ist jederzeit moeglich und wird beim naechsten
Start exakt fortgesetzt. Unveraenderte Quellen werden ueber den `content_hash`
nie erneut verarbeitet.

## Was die Pipeline nicht selbst entscheidet

Sechs Punkte bleiben bewusst offen und landen in `outputs/DECISIONS.md`:
unbelegte Dichtewerte, Bereichskonflikte, mehrdeutige LV Zuordnungen,
unklare Dokumenttypen, Verwerfen von Datensaetzen und jede Abweichung vom
Auftragsprompt. Der Grund ist einfach: eine autonom geratene Annahme ist im
Managementbericht nicht als Annahme sichtbar, sondern als saubere Zahl.

## Datenschutz

`data/`, `work/`, `outputs/` und `METHOD.md` stehen in `.gitignore`. Sie
enthalten Lieferanten-, Projekt- und Personendaten und gehoeren nicht in ein
oeffentliches Repository.

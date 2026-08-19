# Rohtextfixtures

Die Dateien `lieferschein_*.txt` sind **nachgebildete** Lieferscheintexte des
Lieferanten Baustoff Vertrieb Fulda Werra. Sie folgen den Bezeichnungen und
Nummernkreisen aus dem IFS Wareneingang, stammen aber nicht aus einem gescannten
Originalbeleg: fuer diesen Lauf lagen keine Lieferschein PDFs vor.

Sobald echte PDFs vorliegen, werden die Fixtures durch Rohtexte aus
`work/text/` ersetzt und die Vorlage in
`config/supplier_templates/baustoff_vertrieb_fulda_werra.yaml` daran geschaerft.
Dieser Punkt steht als offener Punkt in `outputs/DECISIONS.md`.

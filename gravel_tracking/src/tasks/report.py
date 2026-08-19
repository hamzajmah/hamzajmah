"""T6 Abschlussbericht und Methodendokumentation."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date

from ..harness import Context, TaskResult
from ..materials import CHARGE_SUPPLY
from ..state import DONE, PARKED, Task


def _fmt(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def run(task: Task, ctx: Context) -> TaskResult:
    state = ctx.state
    records = [r for r in ctx.store.records() if not r.is_duplicate]
    supply = [r for r in records if r.charge_type == CHARGE_SUPPLY]

    quality_path = ctx.work_dir / "quality.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else {}

    by_area: dict[str, dict[str, float]] = defaultdict(lambda: {"t": 0.0, "m3": 0.0, "records": 0})
    for rec in supply:
        bucket = by_area[rec.area_final or "GENERAL"]
        bucket["t"] += rec.quantity_t or 0.0
        bucket["m3"] += rec.delivered_m3_installed or 0.0
        bucket["records"] += 1

    by_material: dict[str, float] = defaultdict(float)
    for rec in supply:
        by_material[f"{rec.material_class} {rec.grain_size}".strip()] += rec.quantity_t or 0.0

    by_month: dict[str, float] = defaultdict(float)
    for rec in supply:
        if rec.delivery_date:
            by_month[rec.delivery_date.strftime("%Y-%m")] += rec.quantity_t or 0.0

    methods = Counter(r.extraction_method for r in records)
    escalations = Counter(t.escalation_level for t in state.tasks.values())
    parked = [t for t in state.tasks.values() if t.status == PARKED]
    counts = state.counts()

    disposal_t = sum(r.quantity_t or 0.0 for r in records if r.charge_type == "disposal_acceptance")
    surcharge_t = sum(r.quantity or 0.0 for r in records if r.charge_type in ("surcharge", "freight") and r.unit.lower() == "ton")
    total_t = sum(r.quantity_t or 0.0 for r in supply)
    total_m3 = sum(r.delivered_m3_installed or 0.0 for r in supply)
    m3_low = sum(r.delivered_m3_installed_low or 0.0 for r in supply)
    m3_high = sum(r.delivered_m3_installed_high or 0.0 for r in supply)

    provisional = bool(quality.get("result_provisional"))
    lines: list[str] = []
    add = lines.append
    add(f"# Laufbericht Schotter Tracking - {ctx.cfg['project']['name']}")
    add("")
    add(f"Stichtag des Laufs: {date.today().isoformat()}  ")
    add(f"Betrachtungszeitraum: {ctx.cfg['project']['period_start']} bis {ctx.cfg['project']['period_end']}  ")
    add(f"Auftragnehmer: {ctx.cfg['project']['contractor']}  ")
    add(f"Quellsystem: {ctx.cfg['project']['erp_system']}")
    add("")
    if provisional:
        add("> **Das Ergebnis ist vorlaeufig.** Der Anteil der Menge in der Pruefliste liegt bei "
            f"{quality.get('review_share_pct', 0):.2f} Prozent und damit ueber der Schwelle von "
            f"{quality.get('review_share_threshold_pct', 5)} Prozent.")
        add("")
    add("## 1. Ergebnis in einer Zeile")
    add("")
    add(f"Erfasst sind **{_fmt(total_t)} t Schotter und Mineralgemisch** aus {len(supply)} Positionen. "
        f"Umgerechnet sind das **{_fmt(total_m3)} m3 eingebaut** (Bandbreite {_fmt(m3_low)} bis {_fmt(m3_high)} m3 bei Dichte plus minus 10 Prozent). "
        "Die Umrechnung beruht auf angenommenen Dichten, nicht auf Projektbelegen.")
    add("")
    add("## 2. Lauf und Budget")
    add("")
    add(f"- Laeufe insgesamt: {state.meta['runs']}")
    # Dieser Bericht laeuft selbst noch als Task, deshalb plus eins.
    add(f"- Tasks: {counts.get(DONE, 0) + 1} erledigt, {counts.get(PARKED, 0)} geparkt, {counts.get('pending', 0)} offen")
    add(f"- Checkpoints bis zu diesem Bericht: {state.meta['checkpoints']}")
    add(f"- LLM Aufrufe: {state.meta['llm_calls']} (Budget {ctx.cfg['budget']['max_llm_calls']})")
    add(f"- Abbruchgrund: {state.meta.get('abort_reason') or 'kein Abbruch, Warteschlange leer'}")
    add("")
    add("### Eskalationsverteilung")
    add("")
    add("| Stufe | Tasks |")
    add("|---|---|")
    for level in sorted(escalations):
        add(f"| {level} | {escalations[level]} |")
    add("")
    add("### Extraktionsverfahren")
    add("")
    add("| Verfahren | Saetze | Anteil |")
    add("|---|---|---|")
    for method, count in methods.most_common():
        add(f"| {method} | {count} | {_fmt(100.0 * count / max(len(records), 1), 1)} Prozent |")
    add("")
    ocr_share = 100.0 * sum(v for k, v in methods.items() if "ocr" in k) / max(len(records), 1)
    llm_share = 100.0 * sum(v for k, v in methods.items() if "llm" in k) / max(len(records), 1)
    add(f"Anteil OCR: {_fmt(ocr_share, 1)} Prozent. Anteil LLM Extraktion: {_fmt(llm_share, 1)} Prozent.")
    add("")
    if parked:
        add("### Geparkte Tasks")
        add("")
        add("| Task | Typ | Stufe | Grund |")
        add("|---|---|---|---|")
        for t in sorted(parked, key=lambda t: t.task_id)[:50]:
            add(f"| {t.task_id} | {t.type} | {t.escalation_level} | {t.last_error_class}: {t.last_error[:120]} |")
        add("")
    add("## 3. Menge je Bereich")
    add("")
    add("| Bereich | Positionen | Liefermenge t | m3 eingebaut | Anteil an Gesamtmenge |")
    add("|---|---:|---:|---:|---:|")
    for area in sorted(by_area, key=lambda a: -by_area[a]["t"]):
        b = by_area[area]
        share = 100.0 * b["t"] / total_t if total_t else 0.0
        add(f"| {area} | {int(b['records'])} | {_fmt(b['t'], 2)} | {_fmt(b['m3'], 2)} | {_fmt(share, 1)} Prozent |")
    add(f"| **Summe** | **{len(supply)}** | **{_fmt(total_t, 2)}** | **{_fmt(total_m3, 2)}** | **100,0 Prozent** |")
    add("")
    add("## 4. Menge je Material")
    add("")
    add("| Material | Liefermenge t |")
    add("|---|---:|")
    for material in sorted(by_material, key=lambda m: -by_material[m]):
        add(f"| {material} | {_fmt(by_material[material], 2)} |")
    add("")
    add("## 5. Menge je Monat")
    add("")
    add("| Monat | Liefermenge t |")
    add("|---|---:|")
    for month in sorted(by_month):
        add(f"| {month} | {_fmt(by_month[month], 2)} |")
    add("")
    add("## 6. Abgegrenzte Mengen")
    add("")
    add(f"- Annahme Erdaushub und Bohrklein (Gegenrichtung, keine Lieferung): **{_fmt(disposal_t, 2)} t**")
    add(f"- Tonnen auf Zuschlags- und Frachtzeilen (reine Abrechnungsbasis, nicht in der Liefermenge): **{_fmt(surcharge_t, 2)} t**")
    add("")
    add("## 7. Doppelbuchungen")
    add("")
    add(f"- Als Dublette erkannt und genau einmal gezaehlt: **{quality.get('duplicates_removed', 0)} Zeilen** "
        "(gleiche Lieferscheinnummer, gleiches Datum, gleiche Positionsart, gleiche Menge). Liste in `work/03_duplicates.csv`.")
    add("- Fuer die Mengenauswertung ist der Fall bereinigt. Fuer die Rechnungspruefung ist er offen, weil die Buchung im ERP zweimal steht.")
    add("")
    add("## 8. Datenqualitaet")
    add("")
    for key, value in sorted(quality.items()):
        add(f"- {key}: {value}")
    add("")
    add("## 9. Offene Punkte aus DECISIONS.md")
    add("")
    decisions = ctx.decisions.items()
    if decisions:
        for d in decisions:
            add(f"- **[{d.category}] {d.topic}** - {d.impact}")
    else:
        add("- keine")
    add("")
    add("## 10. Was dieser Bericht ausdruecklich nicht sagt")
    add("")
    add("- Er sagt nichts ueber Verluste oder Mehrverbrauch. Solange die Pruefliste nicht abgearbeitet ist, waere jede solche Aussage eine Vermutung.")
    add("- Er enthaelt keinen LV Abgleich, solange kein Aufmass hinterlegt ist.")
    add("- Er enthaelt keine Preise, weil der ERP Export keine enthaelt.")
    add("")

    report_path = ctx.output_dir / "run_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")

    _write_method(ctx, quality, total_t, total_m3, m3_low, m3_high)
    ctx.log(f"REPORT geschrieben, menge_gesamt_t={total_t:.2f} bereiche={len(by_area)}")
    return TaskResult(ok=True, message="Bericht geschrieben", data={"areas": len(by_area), "supply_t": round(total_t, 2)})


def _write_method(ctx: Context, quality: dict, total_t: float, total_m3: float, m3_low: float, m3_high: float) -> None:
    factors = ctx.factors.get("factors") or {}
    lines = [
        "# METHOD - wie diese Zahlen entstanden sind",
        "",
        f"Stichtag: {date.today().isoformat()}",
        "",
        "## 1. Datenquellen",
        "",
        "| Quelle | Inhalt | Stand |",
        "|---|---|---|",
    ]
    inventory = ctx.work_dir / "00_inventory.csv"
    if inventory.exists():
        import csv as _csv

        with inventory.open("r", encoding="utf-8", newline="") as fh:
            for row in _csv.DictReader(fh, delimiter=";"):
                lines.append(f"| `{row['path']}` | {row['doc_type_guess']} | content_hash {row['content_hash'][:8]} |")
    lines += [
        "",
        "Die Bereichszuordnung stammt aus dem Feld `Sub Project ID` des IFS Wareneingangs. "
        "Sie ist die fachliche Sortierung und wird durch keinen automatischen Schritt ueberschrieben.",
        "",
        "## 2. Positionsarten",
        "",
        "| charge_type | Bedeutung | Zaehlt in der Liefermenge |",
        "|---|---|---|",
        "| material_supply | Schotter, Mineralgemisch, Sand | ja |",
        "| disposal_acceptance | Annahme Erdaushub und Bohrklein beim Lieferanten | nein, eigene Kennzahl |",
        "| surcharge | Diesel-, Energie-, Samstagszuschlag | nein, Tonnen sind nur Abrechnungsbasis |",
        "| freight | Frachtkosten und Frachtkostenausgleich | nein |",
        "",
        "Diese Trennung ist die wichtigste Einzelentscheidung der Auswertung. Ohne sie waere die "
        "ausgewiesene Liefermenge um die Tonnen der Zuschlagszeilen zu hoch.",
        "",
        "## 3. Umrechnung Tonnen in Kubikmeter",
        "",
        "| Faktor | lose t/m3 | eingebaut t/m3 | Verdichtung | Quelle | Konfidenz |",
        "|---|---:|---:|---:|---|---|",
    ]
    for key in sorted(factors):
        f = factors[key]
        lines.append(
            f"| {key} | {f['bulk_density_t_per_m3']} | {f['installed_density_t_per_m3']} | "
            f"{f['compaction_factor']} | {f['source']} | {f['confidence']} |"
        )
    lines += [
        "",
        "Der Vergleich gegen das LV erfolgt ausschliesslich gegen `delivered_m3_installed`, also gegen das "
        "verdichtete Einbauvolumen. Ein Vergleich mit der losen Schuettdichte wuerde ein Scheindelta von "
        "rund 20 Prozent erzeugen.",
        "",
        f"Sensitivitaet bei Dichte plus minus 10 Prozent: {total_m3:,.0f} m3 liegt zwischen {m3_low:,.0f} und {m3_high:,.0f} m3.".replace(",", "."),
        "",
        "## 4. Dedup und Plausibilitaet",
        "",
        "- Dedup Schluessel: `supplier_name + delivery_note_no + delivery_date + charge_type + material_class + grain_size`. "
        "Die Positionsart geht in den Schluessel ein, weil eine Fuhre im ERP mehrere Zeilen erzeugt (Material, Diesel-, Samstagszuschlag).",
        "- Es nehmen nur Saetze mit einer aus dem Dokument gelesenen Lieferscheinnummer an der Dedup teil.",
        "- Bei gleicher Nummer und abweichender Menge wird nichts verworfen, alle Saetze bleiben in der Menge und gehen in die Pruefliste.",
        "- Plausibilitaetsregeln: Menge je Lieferung 5 bis 32 t, Datum im Projektzeitraum und nicht in der Zukunft, Koernung erkannt.",
        "",
        "## 5. Bekannte Luecken",
        "",
        f"- Anteil der Menge in der Pruefliste: {quality.get('review_share_pct', 0)} Prozent (Schwelle {quality.get('review_share_threshold_pct', 5)} Prozent).",
        "- Es liegen keine Lieferschein PDFs vor. Die Auswertung stuetzt sich vollstaendig auf den IFS Wareneingang.",
        "- Es liegt kein Leistungsverzeichnis und kein Aufmassstand vor.",
        "- Der ERP Export enthaelt keine Preise.",
        "",
        "## 6. Was diese Auswertung nicht zeigt",
        "",
        "- Keinen Soll-Ist-Vergleich gegen das LV, solange kein Aufmass hinterlegt ist.",
        "- Keine Materialkosten, keine Erloese, keine Marge.",
        "- Keine Aussage zu Verlusten, Bodenaustausch oder Nachtragspotenzial. Solche Einordnungen sind Hypothesen "
        "und werden erst nach Abarbeitung der Pruefliste und Freigabe der Dichtewerte belastbar.",
        "- Keine Aussage darueber, wo das Material tatsaechlich eingebaut wurde. Die Auswertung zeigt, wohin es gebucht wurde.",
        "",
        "## 7. Anbindung in Power BI",
        "",
        "Die Arbeitsmappe `outputs/powerbi/gravel_model.xlsx` enthaelt je Blatt genau eine Tabelle (ListObject) "
        "mit stabilem Namen. Power BI verbindet sich auf das ListObject, nicht auf das Blatt.",
        "",
        "1. **Power BI Desktop, lokal:** Datei aus dem OneDrive Synchronisationspfad laden, "
        "z.B. `C:\\Users\\<Benutzer>\\<Organisation>\\<Projektbibliothek>\\gravel_model.xlsx`. "
        "Der Ordner wird in Power BI als Parameter `DataFolder` angelegt, damit der Pfad beim Wechsel der Umgebung nur an einer Stelle geaendert wird.",
        "2. **Power BI Service, Aktualisierung:** SharePoint Ordner Konnektor mit der Bibliotheks URL "
        "(`https://<tenant>.sharepoint.com/sites/<site>/Freigegebene%20Dokumente/...`), danach Filter auf den Dateinamen. "
        "Ueber den Ordner Konnektor funktioniert die geplante Aktualisierung ohne Gateway.",
        "3. Beim Wechsel zwischen beiden Wegen wird ausschliesslich der Parameter `DataFolder` umgestellt.",
        "",
        "## 8. Reproduktion",
        "",
        "```bash",
        "python -m src.cli run --until-done      # vollstaendiger Lauf",
        "python -m src.cli resume                # Fortsetzung nach Abbruch",
        "python -m src.cli run --since 2026-06-01  # inkrementell ab Datum",
        "python -m src.cli status                # Zustand der Warteschlange",
        "```",
        "",
    ]
    (ctx.cfg.root / "METHOD.md").write_text("\n".join(lines), encoding="utf-8")

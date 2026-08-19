"""T4c Rechnungsliste aus IFS auswerten.

Die Liste enthaelt keine Mengen und keine Orte, dafuer zwei Dinge, die sonst
fehlen: die **Bestellnummern**, unter denen Material eingekauft wurde, und die
**Rechnungsbetraege**. Damit laesst sich pruefen, ob der ausgewertete
Mengenbestand alle Bestellungen abdeckt.

Die Rechnungsnummer traegt dasselbe Format wie das Feld Receipt Reference im
Wareneingang (D-JJnnnnnnn). Sobald beide Seiten denselben Zeitraum abdecken,
verbindet dieser Schluessel Kosten und Menge.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..decisions import Decision
from ..harness import Context, TaskResult
from ..state import Task

INVOICE_COLUMNS = [
    "po_reference", "invoices", "cancelled", "net_amount_eur", "first_invoice", "last_invoice",
    "receipts_in_scope", "coverage_note",
]


@dataclass
class OrderSummary:
    """Rechnungen je Bestellung."""

    invoices: int = 0
    cancelled: int = 0
    net_amount: float = 0.0
    days: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.days is None:
            self.days = []

    @property
    def first(self) -> str:
        return min(self.days) if self.days else ""

    @property
    def last(self) -> str:
        return max(self.days) if self.days else ""


def run(task: Task, ctx: Context) -> TaskResult:
    path = ctx.cfg.path("invoice_list")
    if path is None or not path.is_file():
        return TaskResult(ok=True, message="keine Rechnungsliste hinterlegt", data={"invoices": 0})

    import pandas as pd

    frame = pd.read_excel(path, sheet_name=ctx.cfg.get("invoice_list", {}).get("sheet", 0))
    frame = frame[frame["Invoice No"].notna() & frame["PO Reference"].notna()]

    # Welche Bestellungen kennt der Mengenbestand?
    orders_with_quantity = {r.order_no for r in ctx.store.records() if r.order_no}

    rows = []
    by_order: dict[str, OrderSummary] = defaultdict(OrderSummary)
    for record in frame.to_dict("records"):
        order = str(record.get("PO Reference") or "").strip()
        bucket = by_order[order]
        bucket.invoices += 1
        if str(record.get("Status") or "").lower() == "cancelled":
            bucket.cancelled += 1
        else:
            bucket.net_amount += float(record.get("Net Amount") or 0.0)
        day = str(record.get("Invoice Date") or "")[:10]
        if day:
            bucket.days.append(day)

    missing_orders = []
    for order in sorted(by_order):
        bucket = by_order[order]
        in_scope = order in orders_with_quantity
        if not in_scope:
            missing_orders.append((order, bucket))
        rows.append({
            "po_reference": order, "invoices": bucket.invoices, "cancelled": bucket.cancelled,
            "net_amount_eur": round(bucket.net_amount, 2),
            "first_invoice": bucket.first, "last_invoice": bucket.last,
            "receipts_in_scope": "ja" if in_scope else "nein",
            "coverage_note": "Mengen liegen vor" if in_scope else "Rechnungen ohne zugehoerigen Mengenbestand",
        })

    _write(ctx.work_dir / "12_invoices_by_order.csv", INVOICE_COLUMNS, rows)

    if missing_orders:
        detail = "; ".join(
            f"{order}: {b.invoices} Rechnungen, {b.net_amount:,.0f} EUR netto, {b.first} bis {b.last}".replace(",", ".")
            for order, b in missing_orders
        )
        ctx.decisions.add(Decision(
            category=3,
            topic="Bestellungen mit Rechnungen, aber ohne Mengenbestand",
            detail=f"Die Rechnungsliste nennt Bestellungen, zu denen kein Wareneingangsexport vorliegt: {detail}.",
            impact=(
                "Fuer diese Bestellungen ist bekannt, dass eingekauft und bezahlt wurde, aber nicht wie viel, "
                "welches Material und wohin. Die ausgewiesene Liefermenge ist entsprechend unvollstaendig."
            ),
            proposal="Wareneingangsexport je genannter Bestellung ziehen und in data/erp/ ablegen. Der naechste Lauf nimmt ihn auf.",
            evidence="work/12_invoices_by_order.csv",
        ))

    total_net = round(sum(b.net_amount for b in by_order.values()), 2)
    ctx.log(f"INVOICES bestellungen={len(by_order)} rechnungen={len(frame)} netto={total_net:.0f} ohne_menge={len(missing_orders)}")
    return TaskResult(ok=True, message=f"{len(frame)} Rechnungen, {len(by_order)} Bestellungen", data={
        "invoices": len(frame), "orders": len(by_order), "net_amount_eur": total_net,
        "orders_without_quantity": [o for o, _ in missing_orders],
    })


def _write(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, delimiter=";", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})

"""Registry der Tasktypen."""
from __future__ import annotations

from . import (
    clean,
    extract_erp,
    extract_pdf,
    extract_tracking,
    inventory,
    invoices,
    locations,
    lv_match,
    management_report,
    master_data,
    model_build,
    reconcile,
    report,
    text,
)

HANDLERS = {
    "inventory": inventory.run,
    "master_data": master_data.run,
    "text_extract": text.run,
    "extract_pdf": extract_pdf.run,
    "extract_erp": extract_erp.run,
    "extract_tracking": extract_tracking.run,
    "clean": clean.run,
    "locations": locations.run,
    "lv_match": lv_match.run,
    "reconcile": reconcile.run,
    "invoices": invoices.run,
    "build_model": model_build.run,
    "report": report.run,
    "management_report": management_report.run,
}

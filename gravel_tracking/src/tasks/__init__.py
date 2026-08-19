"""Registry der Tasktypen."""
from __future__ import annotations

from . import clean, extract_erp, extract_pdf, inventory, locations, lv_match, model_build, report, text

HANDLERS = {
    "inventory": inventory.run,
    "text_extract": text.run,
    "extract_pdf": extract_pdf.run,
    "extract_erp": extract_erp.run,
    "clean": clean.run,
    "locations": locations.run,
    "lv_match": lv_match.run,
    "build_model": model_build.run,
    "report": report.run,
}

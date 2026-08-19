"""T1 Textgewinnung.

Textlayer bevorzugt, OCR nur bei Bedarf. Der Rohtext je Seite landet in
work/text/. Die Eskalationsstufe des Tasks steuert das Verfahren.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from ..config import load_supplier_templates
from ..harness import Context, TaskResult
from ..state import Task

PAGE_SEPARATOR = "\n\n----- PAGE {n} -----\n"


def text_path(ctx: Context, source_id: str, content_hash: str) -> Path:
    safe = source_id.replace("/", "__")
    return ctx.work_dir / "text" / f"{safe}.{content_hash[:8]}.txt"


def _extract_text_layer(pdf_path: Path) -> str:
    import pdfplumber

    parts = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for n, page in enumerate(pdf.pages, 1):
            parts.append(PAGE_SEPARATOR.format(n=n))
            parts.append(page.extract_text() or "")
    return "".join(parts)


def _ocr(pdf_path: Path, dpi: int, deskew: bool, language: str) -> str:
    """OCR ueber ocrmypdf. Fehlt das Werkzeug, scheitert der Task sauber."""
    if shutil.which("ocrmypdf") is None:
        raise FileNotFoundError("ocrmypdf ist nicht installiert")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "ocr.pdf"
        cmd = ["ocrmypdf", "--force-ocr", "--language", language, "--image-dpi", str(dpi)]
        if deskew:
            cmd += ["--deskew", "--clean"]
        cmd += [str(pdf_path), str(out)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=900)
        return _extract_text_layer(out)


def run(task: Task, ctx: Context) -> TaskResult:
    pdf_path = ctx.cfg.root / task.source_file
    level = task.escalation_level
    ex = ctx.cfg["extraction"]

    if level == 0:
        method, text = "text_layer", _extract_text_layer(pdf_path)
    elif level == 1:
        method, text = "ocr_standard", _ocr(pdf_path, 300, False, ex["ocr_language"])
    elif level == 2:
        method, text = "ocr_highres", _ocr(pdf_path, 600, True, ex["ocr_language"])
    else:
        return TaskResult(
            ok=False,
            message="Textgewinnung auf Stufe 0 bis 2 erfolglos, hoehere Stufen sind LLM gestuetzt und nicht konfiguriert",
            error_class="text_extraction_exhausted",
        )

    out_path = text_path(ctx, task.source_file, task.content_hash)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")

    # Abnahme: Textlaenge ueber Mindestschwelle und mindestens ein Ankerbegriff.
    min_chars = int(ex["min_text_chars_per_page"])
    stripped = "".join(c for c in text if not c.isspace())
    if len(stripped) < min_chars:
        return TaskResult(ok=False, message=f"Text zu kurz ({len(stripped)} Zeichen)", error_class="text_too_short")

    anchors = [a for tpl in load_supplier_templates(ctx.cfg) for a in tpl.get("anchors", [])]
    low = text.lower()
    if anchors and not any(a.lower() in low for a in anchors):
        return TaskResult(ok=False, message="kein Ankerbegriff eines bekannten Lieferanten gefunden", error_class="no_supplier_anchor")

    return TaskResult(ok=True, message=f"{len(stripped)} Zeichen", data={"method": method, "text_path": str(out_path.relative_to(ctx.cfg.root))})

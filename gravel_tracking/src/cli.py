"""Kommandozeile der Pipeline. Keine interaktiven Eingaben zur Laufzeit."""
from __future__ import annotations

import argparse
import json
import sys

from .config import load_config, load_conversion_factors
from .decisions import DecisionLog
from .harness import Context, Harness
from .state import DONE, PARKED, PENDING, State, Task
from .store import RecordStore
from .tasks import HANDLERS

INVENTORY_TASK_ID = "T0:inventory"


def build_context(config_path: str | None, since: str = "") -> Context:
    cfg = load_config(config_path)
    work_dir = cfg.path("work_dir") or (cfg.root / "work")
    output_dir = cfg.path("output_dir") or (cfg.root / "outputs")
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    state = State.load(work_dir / "state.json")
    return Context(
        cfg=cfg,
        factors=load_conversion_factors(str(cfg.root / "config" / "conversion_factors.yaml")),
        state=state,
        store=RecordStore(work_dir / "02_records.csv"),
        decisions=DecisionLog(output_dir / "DECISIONS.md"),
        work_dir=work_dir,
        output_dir=output_dir,
        log_path=work_dir / "run.log",
        since=since,
    )


def _queue_inventory(ctx: Context) -> None:
    """Der Inventarscan laeuft bei jedem Start erneut und erkennt neue oder
    geaenderte Quellen. Unveraenderte Quellen erzeugen keine neue Arbeit."""
    task = ctx.state.tasks.get(INVENTORY_TASK_ID)
    if task is None:
        ctx.state.add(Task(task_id=INVENTORY_TASK_ID, type="inventory", priority=10))
    else:
        task.status = PENDING
        task.attempts_at_level = 0
        task.escalation_level = 0


def cmd_run(args: argparse.Namespace) -> int:
    ctx = build_context(args.config, since=args.since or "")
    if args.reset:
        ctx.state.tasks.clear()
    _queue_inventory(ctx)
    ctx.log(f"RUN start until_done={args.until_done} since={args.since or '-'} max_tasks={args.max_tasks or '-'}")
    harness = Harness(ctx, HANDLERS)
    reason = harness.run(max_tasks=args.max_tasks)
    ctx.state.meta["abort_reason"] = reason
    ctx.state.save()
    ctx.log(f"RUN ende grund={reason or 'warteschlange leer'}")
    print(_status_text(ctx, reason))
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    ctx = build_context(args.config, since=args.since or "")
    ctx.log("RESUME start")
    harness = Harness(ctx, HANDLERS)
    reason = harness.run(max_tasks=args.max_tasks)
    ctx.state.meta["abort_reason"] = reason
    ctx.state.save()
    ctx.log(f"RESUME ende grund={reason or 'warteschlange leer'}")
    print(_status_text(ctx, reason))
    return 0


def cmd_single(task_type: str):
    def _cmd(args: argparse.Namespace) -> int:
        ctx = build_context(args.config, since=getattr(args, "since", "") or "")
        _queue_inventory(ctx)
        harness = Harness(ctx, HANDLERS)
        # Nur Tasks bis einschliesslich des gewuenschten Typs ausfuehren.
        allowed_priority = {"inventory": 10, "extract": 30, "clean": 50, "match": 60, "build": 70}[task_type]
        for task in ctx.state.tasks.values():
            if task.priority > allowed_priority and task.status == PENDING:
                task.status = PARKED
                task.last_error_class = "uebersprungen_durch_teilkommando"
        reason = harness.run()
        ctx.state.save()
        print(_status_text(ctx, reason))
        return 0

    return _cmd


def cmd_status(args: argparse.Namespace) -> int:
    ctx = build_context(args.config)
    print(_status_text(ctx, ctx.state.meta.get("abort_reason", "")))
    return 0


def _status_text(ctx: Context, reason: str) -> str:
    counts = ctx.state.counts()
    quality_path = ctx.work_dir / "quality.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.exists() else {}
    parked = [t for t in ctx.state.tasks.values() if t.status == PARKED]
    lines = [
        "Zustand der Warteschlange",
        f"  erledigt: {counts.get(DONE, 0)}  offen: {counts.get(PENDING, 0)}  geparkt: {counts.get(PARKED, 0)}",
        f"  Saetze: {len(ctx.store)}  Liefermenge t: {ctx.store.total_t()}",
        f"  offene Entscheidungen: {len(ctx.decisions.items())}",
        f"  Abbruchgrund: {reason or 'kein Abbruch, Warteschlange leer'}",
    ]
    if quality:
        lines.append(f"  Pruefliste: {quality.get('review_share_pct')} Prozent der Menge, vorlaeufig={quality.get('result_provisional')}")
    for task in sorted(parked, key=lambda t: t.task_id)[:10]:
        lines.append(f"  geparkt: {task.task_id} ({task.last_error_class})")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.cli", description="Schotter Tracking und Mengenabgleich")
    parser.add_argument("--config", default=None, help="Pfad zu config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--since", default="", help="nur Belege ab diesem Datum (YYYY-MM-DD)")
        p.add_argument("--max-tasks", type=int, default=None, dest="max_tasks", help="Abbruch nach n Tasks, fuer Tests")

    p_run = sub.add_parser("run", help="Pipeline ausfuehren")
    p_run.add_argument("--until-done", action="store_true", dest="until_done", default=True)
    p_run.add_argument("--reset", action="store_true", help="Zustand verwerfen und neu aufbauen")
    add_common(p_run)
    p_run.set_defaults(func=cmd_run)

    p_resume = sub.add_parser("resume", help="abgebrochenen Lauf fortsetzen")
    add_common(p_resume)
    p_resume.set_defaults(func=cmd_resume)

    for name, task_type in [("inventory", "inventory"), ("extract", "extract"), ("clean", "clean"), ("match", "match"), ("build", "build")]:
        p = sub.add_parser(name, help=f"nur Schritt {name}")
        add_common(p)
        p.set_defaults(func=cmd_single(task_type))

    p_status = sub.add_parser("status", help="Zustand anzeigen")
    p_status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())

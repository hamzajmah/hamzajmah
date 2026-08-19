"""Schleife B: Laufzeitschleife der Pipeline.

Arbeitet dokumentweise, nicht phasenweise, und laeuft bis die Warteschlange
leer oder das Budget erschoepft ist. Ein Abbruch ist ein normaler Ausgang: er
endet immer mit geschriebenem Zustand und run_report.md.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .decisions import DecisionLog
from .state import DONE, PARKED, PENDING, RUNNING, State, Task, now_iso
from .store import RecordStore


class SchemaChangeRequired(RuntimeError):
    """Harter Abbruch: ohne Schemaaenderung kommt der Lauf nicht weiter."""


@dataclass
class TaskResult:
    ok: bool
    message: str = ""
    error_class: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    escalate: bool = True     # False = Wiederholung auf gleicher Stufe sinnlos


@dataclass
class Context:
    cfg: Config
    factors: dict[str, Any]
    state: State
    store: RecordStore
    decisions: DecisionLog
    work_dir: Path
    output_dir: Path
    log_path: Path
    since: str = ""

    def log(self, line: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} {line}\n")


Handler = Callable[[Task, Context], TaskResult]


class Harness:
    def __init__(self, ctx: Context, handlers: Mapping[str, Handler]) -> None:
        self.ctx = ctx
        self.handlers = handlers
        self.budget = ctx.cfg["budget"]
        self.started = time.monotonic()
        self.processed_this_run = 0
        self.abort_reason = ""
        # Die Abbruchkriterien beziehen sich auf den laufenden Durchlauf. Sonst
        # koennte ein Lauf nach einem Abbruch nie wieder aufgenommen werden.
        self.outcomes: list[dict[str, Any]] = []

    # -- Budget und Abbruch ---------------------------------------------
    def _runtime_minutes(self) -> float:
        return (time.monotonic() - self.started) / 60.0

    def _budget_exhausted(self) -> str:
        if self._runtime_minutes() > float(self.budget["max_runtime_minutes"]):
            return "max_runtime_minutes erreicht"
        if self.ctx.state.meta["llm_calls"] >= int(self.budget["max_llm_calls"]):
            return "max_llm_calls erreicht"
        limit = float(self.budget.get("max_cost_eur") or 0.0)
        if limit and self.ctx.state.meta["cost_eur"] >= limit:
            return "max_cost_eur erreicht"
        return ""

    def _abort_condition(self) -> str:
        outcomes = self.outcomes
        window = int(self.budget["abort_error_rate_window"])
        recent = outcomes[-window:]
        if len(recent) == window:
            errors = sum(1 for o in recent if o["ok"] is False)
            rate = 100.0 * errors / window
            if rate > float(self.budget["abort_error_rate_pct"]):
                return f"Fehlerquote der letzten {window} Tasks bei {rate:.0f} Prozent"
        streak = int(self.budget["abort_same_error_streak"])
        tail = [o for o in outcomes[-streak:] if o["ok"] is False]
        if len(tail) == streak and len({o["error_class"] for o in tail}) == 1:
            return f"{streak} aufeinanderfolgende Tasks mit Fehlerklasse {tail[0]['error_class']}"
        return ""

    # -- Hauptschleife ---------------------------------------------------
    def run(self, max_tasks: int | None = None) -> str:
        state = self.ctx.state
        state.meta["runs"] += 1
        checkpoint_every = int(self.budget["checkpoint_every"])

        while True:
            reason = self._budget_exhausted()
            if reason:
                self.abort_reason = reason
                break
            reason = self._abort_condition()
            if reason:
                self.abort_reason = reason
                break
            if max_tasks is not None and self.processed_this_run >= max_tasks:
                self.abort_reason = f"Testabbruch nach {max_tasks} Tasks"
                break

            task = state.next_task()
            if task is None:
                break

            task.status = RUNNING
            task.updated_at = now_iso()
            try:
                result = self._execute(task)
            except SchemaChangeRequired as exc:
                task.status = PENDING
                task.last_error = str(exc)
                self.abort_reason = f"Schemaaenderung noetig: {exc}"
                break
            except Exception as exc:  # defensiv: ein Task darf den Lauf nicht killen
                result = TaskResult(ok=False, message=f"{type(exc).__name__}: {exc}", error_class=type(exc).__name__)

            self._apply(task, result)
            self.processed_this_run += 1
            state.meta["processed"] += 1

            if self.processed_this_run % checkpoint_every == 0:
                self.checkpoint()

        self.checkpoint()
        return self.abort_reason

    def _execute(self, task: Task) -> TaskResult:
        handler = self.handlers.get(task.type)
        if handler is None:
            return TaskResult(ok=False, message=f"kein Handler fuer Tasktyp {task.type}", error_class="no_handler", escalate=False)
        return handler(task, self.ctx)

    def _apply(self, task: Task, result: TaskResult) -> None:
        state = self.ctx.state
        task.attempts += 1
        task.attempts_at_level += 1
        task.updated_at = now_iso()
        outcome = {"ok": result.ok, "error_class": result.error_class or ""}
        self.outcomes.append(outcome)
        state.meta["recent_outcomes"] = (state.meta["recent_outcomes"] + [outcome])[-200:]

        if result.ok:
            task.status = DONE
            task.last_error = ""
            task.last_error_class = ""
            task.result = result.data
            return

        task.last_error = result.message[:500]
        task.last_error_class = result.error_class or "unknown"

        max_attempts = int(self.budget["max_attempts_per_task"])
        max_level = int(self.budget["max_escalation_level"])

        # Nie mehr als drei Versuche je Stufe, nie mehr als ein Aufstieg je Durchlauf.
        if not result.escalate or task.attempts_at_level >= max_attempts:
            task.escalation_level += 1
            task.attempts_at_level = 0

        if task.escalation_level >= max_level:
            task.status = PARKED
            self.ctx.log(f"PARKED {task.task_id} ({task.type}) stufe={task.escalation_level} grund={task.last_error_class}")
        else:
            task.status = PENDING

    # -- Fortschritt ------------------------------------------------------
    def checkpoint(self) -> None:
        state = self.ctx.state
        state.meta["checkpoints"] += 1
        self.ctx.store.save()
        self.ctx.decisions.save()
        state.save()
        counts = state.counts()
        budget_pct = 100.0 * self._runtime_minutes() / float(self.budget["max_runtime_minutes"])
        self.ctx.log(
            f"CHECKPOINT verarbeitet={counts.get(DONE, 0)} offen={counts.get(PENDING, 0) + counts.get(RUNNING, 0)} geparkt={counts.get(PARKED, 0)} "
            f"saetze={len(self.ctx.store)} tonnen_lieferung={self.ctx.store.total_t():.2f} budget={budget_pct:.1f}%"
        )

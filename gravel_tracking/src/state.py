"""Zustandsverwaltung des Harness.

Der Lauf ist jederzeit abbrechbar und wird beim naechsten Start exakt dort
fortgesetzt. Der content_hash verhindert, dass unveraenderte Dokumente erneut
verarbeitet werden.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PENDING, RUNNING, DONE, PARKED, FAILED = "pending", "running", "done", "parked", "failed"
OPEN_STATES = (PENDING, RUNNING)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def file_hash(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()[:32]


@dataclass
class Task:
    task_id: str
    type: str
    priority: int = 50
    source_file: str = ""
    content_hash: str = ""
    status: str = PENDING
    attempts: int = 0
    attempts_at_level: int = 0
    escalation_level: int = 0
    last_error: str = ""
    last_error_class: str = ""
    updated_at: str = field(default_factory=now_iso)
    depends_on: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)


class State:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.tasks: dict[str, Task] = {}
        self.meta: dict[str, Any] = {
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "runs": 0,
            "llm_calls": 0,
            "cost_eur": 0.0,
            "processed": 0,
            "checkpoints": 0,
            "recent_outcomes": [],
            "abort_reason": "",
        }

    # -- Persistenz -----------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> State:
        st = cls(path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            st.meta.update(data.get("meta", {}))
            for tid, raw in data.get("tasks", {}).items():
                st.tasks[tid] = Task(**raw)
            # Ein Lauf, der mitten in einem Task abgebrochen wurde, hinterlaesst
            # running-Tasks. Die werden beim Resume wieder eingereiht.
            for task in st.tasks.values():
                if task.status == RUNNING:
                    task.status = PENDING
        return st

    def save(self) -> None:
        self.meta["updated_at"] = now_iso()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": self.meta,
            "tasks": {tid: asdict(self.tasks[tid]) for tid in sorted(self.tasks)},
        }
        # Atomar schreiben, damit ein Abbruch waehrend des Schreibens den
        # Zustand nicht zerstoert.
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    # -- Taskverwaltung -------------------------------------------------
    def add(self, task: Task) -> bool:
        """Fuegt einen Task hinzu. Bereits bekannter Task bleibt unveraendert.

        Aendert sich der content_hash einer Quelle, wird der Task neu
        eingereiht; unveraenderte Quellen werden nie erneut verarbeitet.
        """
        existing = self.tasks.get(task.task_id)
        if existing is None:
            self.tasks[task.task_id] = task
            return True
        if task.content_hash and existing.content_hash != task.content_hash:
            existing.content_hash = task.content_hash
            existing.status = PENDING
            existing.attempts = 0
            existing.attempts_at_level = 0
            existing.escalation_level = 0
            existing.payload = task.payload
            existing.updated_at = now_iso()
            return True
        return False

    def ready(self) -> list[Task]:
        out = []
        for task in self.tasks.values():
            if task.status not in OPEN_STATES:
                continue
            if all(self.tasks[d].status in (DONE, PARKED) for d in task.depends_on if d in self.tasks):
                out.append(task)
        return sorted(out, key=lambda t: (t.priority, t.task_id))

    def next_task(self) -> Task | None:
        ready = self.ready()
        return ready[0] if ready else None

    def counts(self) -> dict[str, int]:
        c = {PENDING: 0, RUNNING: 0, DONE: 0, PARKED: 0, FAILED: 0}
        for task in self.tasks.values():
            c[task.status] = c.get(task.status, 0) + 1
        return c

    def by_type(self, *types: str) -> Iterable[Task]:
        for tid in sorted(self.tasks):
            if self.tasks[tid].type in types:
                yield self.tasks[tid]

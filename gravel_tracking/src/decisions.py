"""Entscheidungswarteschlange.

Der Harness entscheidet die sechs Punkte aus Abschnitt 4 des Auftragsprompts
niemals selbst. Er sammelt sie hier, rechnet ohne sie weiter und legt sie am
Ende gebuendelt in outputs/DECISIONS.md vor.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

CATEGORIES = {
    1: "Dichtewerte ohne Dokumentbeleg",
    2: "Bereichskonflikt Ordner gegen Dokumentinhalt",
    3: "Mehrdeutige Zuordnung LV Position zu Bereich",
    4: "Dokument ist weder Lieferschein noch Rechnung",
    5: "Verwerfen von Datensaetzen jenseits der Dedup Regel",
    6: "Abweichung vom Auftragsprompt",
}


@dataclass(frozen=True, order=True)
class Decision:
    category: int
    topic: str
    detail: str
    impact: str
    proposal: str
    evidence: str = ""

    @property
    def key(self) -> str:
        return f"{self.category}|{self.topic}"


class DecisionLog:
    """Deduplizierender, deterministisch sortierter Sammler."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._items: dict[str, Decision] = {}
        if path.exists():
            self.load()

    def add(self, decision: Decision) -> None:
        self._items.setdefault(decision.key, decision)

    def items(self) -> list[Decision]:
        return sorted(self._items.values(), key=lambda d: (d.category, d.topic))

    # -- Persistenz -----------------------------------------------------
    @property
    def _json_path(self) -> Path:
        return self.path.with_suffix(".json")

    def load(self) -> None:
        if self._json_path.exists():
            data = json.loads(self._json_path.read_text(encoding="utf-8"))
            for row in data:
                d = Decision(**row)
                self._items[d.key] = d

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._json_path.write_text(
            json.dumps([asdict(d) for d in self.items()], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.path.write_text(self.render(), encoding="utf-8")

    def render(self) -> str:
        lines = [
            "# Entscheidungswarteschlange",
            "",
            "Diese Punkte hat der Harness bewusst **nicht** selbst entschieden.",
            "Er hat ohne sie weitergerechnet; die betroffenen Kennzahlen sind als",
            "Annahme gekennzeichnet. Jeder Punkt braucht eine fachliche Freigabe.",
            "",
            f"Offene Punkte: **{len(self._items)}**",
            "",
        ]
        current = None
        for d in self.items():
            if d.category != current:
                current = d.category
                lines += [f"## {d.category}. {CATEGORIES[d.category]}", ""]
            lines += [
                f"### {d.topic}",
                "",
                f"- **Sachverhalt:** {d.detail}",
                f"- **Auswirkung:** {d.impact}",
                f"- **Vorschlag zur Entscheidung:** {d.proposal}",
            ]
            if d.evidence:
                lines.append(f"- **Beleg:** {d.evidence}")
            lines.append("")
        if not self._items:
            lines += ["_Keine offenen Punkte._", ""]
        return "\n".join(lines)

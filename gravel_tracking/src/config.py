"""Configuration loading. All runtime knobs live in YAML, never in code."""
from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]
    root: Path

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def path(self, key: str) -> Path | None:
        """Absoluter Pfad oder None, wenn nicht konfiguriert."""
        value = self.raw["paths"].get(key, "")
        if not value:
            return None
        return (self.root / value).resolve()

    @property
    def period(self) -> tuple[date, date]:
        p = self.raw["project"]
        return date.fromisoformat(p["period_start"]), date.fromisoformat(p["period_end"])


@functools.lru_cache(maxsize=8)
def load_config(path: str | None = None) -> Config:
    cfg_path = Path(path).resolve() if path else project_root() / "config" / "config.yaml"
    # Konvention: die Konfiguration liegt in <root>/config/config.yaml.
    return Config(raw=_read_yaml(cfg_path), root=cfg_path.parent.parent)


@functools.lru_cache(maxsize=8)
def load_conversion_factors(path: str | None = None) -> dict[str, Any]:
    cfg_path = Path(path).resolve() if path else project_root() / "config" / "conversion_factors.yaml"
    return _read_yaml(cfg_path)


@functools.lru_cache(maxsize=8)
def _load_mapping_file(path: str) -> dict[str, Any]:
    return _read_yaml(Path(path))


def load_lv_mapping(cfg: Config) -> dict[str, Any]:
    """Zuordnung LV Position zu Material, aus config/lv_mapping.yaml."""
    rel = cfg["paths"].get("lv_mapping", "")
    if not rel:
        return {}
    path = cfg.root / rel
    return _load_mapping_file(str(path)) if path.is_file() else {}


def load_supplier_templates(cfg: Config) -> list[dict[str, Any]]:
    templates = []
    for supplier in cfg.get("suppliers", []):
        rel = supplier.get("template")
        if not rel:
            continue
        tpl_path = cfg.root / rel
        if tpl_path.exists():
            templates.append(_read_yaml(tpl_path))
    return templates

"""Loads config.yaml and profile.yaml, resolving `env:VAR` references."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def _resolve_env(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("env:"):
        return os.environ.get(value[4:], "")
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


def load_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `python -m jobbot init` and fill it in."
        )
    with path.open(encoding="utf-8") as f:
        return _resolve_env(yaml.safe_load(f) or {})


@dataclass
class Settings:
    root: Path
    config: dict
    profile: dict

    @property
    def data_dir(self) -> Path:
        d = self.root / self.config.get("paths", {}).get("data_dir", "data")
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobbot.db"

    @property
    def excel_path(self) -> Path:
        return self.root / self.config.get("paths", {}).get("excel", "data/applications.xlsx")

    @property
    def resumes_dir(self) -> Path:
        d = self.data_dir / "resumes"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def section(self, name: str) -> dict:
        return self.config.get(name) or {}


def load_settings(root: Path | None = None) -> Settings:
    root = Path(root or os.environ.get("JOBBOT_HOME", Path.cwd()))
    return Settings(
        root=root,
        config=load_yaml(root / "config.yaml"),
        profile=load_yaml(root / "profile.yaml"),
    )

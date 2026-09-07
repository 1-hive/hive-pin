# SPDX-License-Identifier: GPL-3.0-or-later
"""Operator-configurable limits (SPEC.md section 17).

Resolution order (later wins): built-in defaults -> config file (JSON) ->
environment variables (HIVEPIN_*) -> explicit keyword arguments.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields, replace
from pathlib import Path

from .errors import PinError

_ENV_PREFIX = "HIVEPIN_"


@dataclass(frozen=True)
class Config:
    max_file_bytes: int = 256 * 1024 * 1024
    max_tree_files: int = 50_000
    max_tree_bytes: int = 2 * 1024 * 1024 * 1024
    remote_timeout_seconds: float = 60.0
    materialization_timeout_seconds: float = 300.0
    cache_directory: str = "~/.cache/hivepin"
    repository_registry_path: str = "registry.json"

    # --- derived ---------------------------------------------------------
    @property
    def cache_dir(self) -> Path:
        return Path(self.cache_directory).expanduser()

    def registry_file(self, override: str | None = None) -> Path:
        return Path(override or self.repository_registry_path).expanduser()

    # --- construction ---------------------------------------------------
    @classmethod
    def load(cls, path: str | os.PathLike | None = None, **overrides: object) -> Config:
        cfg = cls()
        file_path = _first_existing(path, os.environ.get(f"{_ENV_PREFIX}CONFIG"))
        if file_path is not None:
            cfg = cfg._merge(_read_file(file_path))
        cfg = cfg._merge(_from_env())
        cfg = cfg._merge({k: v for k, v in overrides.items() if v is not None})
        cfg._validate()
        return cfg

    def _merge(self, values: dict) -> Config:
        known = {f.name for f in fields(self)}
        clean = {}
        for k, v in values.items():
            if k not in known:
                raise PinError("INVALID_REGISTRY", f"unknown config key: {k!r}")
            clean[k] = _coerce(self, k, v)
        return replace(self, **clean)

    def _validate(self) -> None:
        for name in ("max_file_bytes", "max_tree_files", "max_tree_bytes"):
            if getattr(self, name) <= 0:
                raise PinError("INVALID_REGISTRY", f"{name} must be positive")
        for name in ("remote_timeout_seconds", "materialization_timeout_seconds"):
            if getattr(self, name) <= 0:
                raise PinError("INVALID_REGISTRY", f"{name} must be positive")


def _coerce(proto: Config, key: str, value: object):
    current = getattr(proto, key)
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value)
    if isinstance(current, float):
        return float(value)
    return str(value)


def _first_existing(*candidates) -> Path | None:
    for c in candidates:
        if c:
            p = Path(c).expanduser()
            if p.is_file():
                return p
    return None


def _read_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PinError("INVALID_REGISTRY", f"cannot read config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PinError("INVALID_REGISTRY", "config file must be a JSON object")
    return data


def _from_env() -> dict:
    out: dict = {}
    for f in fields(Config):
        env = f"{_ENV_PREFIX}{f.name.upper()}"
        if env in os.environ:
            out[f.name] = os.environ[env]
    return out

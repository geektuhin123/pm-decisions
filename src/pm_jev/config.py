"""Configuration loading: packaged defaults, optionally overlaid by a user file."""

from __future__ import annotations

import os
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    text = resources.files("pm_jev").joinpath("default_config.yaml").read_text(encoding="utf-8")
    config: dict[str, Any] = yaml.safe_load(text) or {}
    path = (os.environ.get("PM_JEV_CONFIG") or "").strip()
    if path:
        user_path = Path(path).expanduser()
        if not user_path.is_file():
            raise FileNotFoundError(f"PM_JEV_CONFIG points to a missing file: {user_path}")
        override = yaml.safe_load(user_path.read_text(encoding="utf-8")) or {}
        if not isinstance(override, dict):
            raise ValueError(f"{user_path} must contain a YAML mapping at the top level")
        # Product areas and reviewers are replaced wholesale so users can drop defaults.
        for replace_key in ("product_areas", "risk_reviewers"):
            if replace_key in override:
                config.pop(replace_key, None)
        config = _deep_merge(config, override)
    return config

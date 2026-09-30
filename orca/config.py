"""Load YAML config + optional .env (keys never required for paper)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config" / "config.yaml"
    if not cfg_path.exists():
        example = ROOT / "config" / "config.example.yaml"
        if not example.exists():
            raise FileNotFoundError(f"No config at {cfg_path} and no example found")
        cfg_path = example

    with cfg_path.open() as f:
        cfg = yaml.safe_load(f) or {}

    mode = os.getenv("ORCA_MODE", cfg.get("mode", "paper")).lower()
    cfg["mode"] = mode
    if mode != "paper":
        # Hard stop: this codebase must not place live orders in this task.
        raise RuntimeError(
            f"ORCA_MODE={mode!r} rejected. Orca is paper-only until live trading "
            "is explicitly approved and implemented. Set ORCA_MODE=paper."
        )

    cfg.setdefault("exchange", {})["apiKey"] = os.getenv("KRAKEN_API_KEY", "")
    cfg.setdefault("exchange", {})["secret"] = os.getenv("KRAKEN_API_SECRET", "")
    return cfg

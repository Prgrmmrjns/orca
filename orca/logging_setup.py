"""Logging helpers for Orca."""

from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(level: str = "INFO", log_dir: str | Path = "logs") -> logging.Logger:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("orca")
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = logging.FileHandler(Path(log_dir) / "orca.log")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger

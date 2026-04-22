from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def setup_logging(log_dir: Path, log_level: str = "INFO") -> None:
    """Configure root logger with console + daily-rotating file handler."""
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, log_level.upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    # Rotating file handler: 10 MB per file, keep 7 backups
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_dir / "llm_router.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid adding duplicate handlers on hot-reload
    if not root.handlers:
        root.addHandler(console_handler)
        root.addHandler(file_handler)
    else:
        root.handlers.clear()
        root.addHandler(console_handler)
        root.addHandler(file_handler)

    # Suppress overly verbose third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)

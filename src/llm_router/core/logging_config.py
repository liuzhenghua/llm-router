from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_DEFAULT_LOG_FORMAT = (
    "%(asctime)s.%(msecs)03d %(levelname)s [%(threadName)s] [%(access_context)s] "
    "[%(filename)s#%(name)s:%(lineno)d] - %(message)s"
)
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _DefaultContextFilter(logging.Filter):
    """Inject default access_context into every app log record that lacks it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "access_context"):
            record.access_context = "-|-|-"  # type: ignore[attr-defined]
        return True


def _make_rotating_handler(path: Path, formatter: logging.Formatter, level: int) -> logging.Handler:
    handler = logging.handlers.RotatingFileHandler(
        filename=path,
        maxBytes=10 * 1024 * 1024,
        backupCount=7,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    handler.setLevel(level)
    return handler


def setup_logging(
    log_dir: Path,
    log_level: str = "INFO",
    log_format: str = _DEFAULT_LOG_FORMAT,
) -> None:
    """Configure root logger → console + app.log."""
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, log_level.upper(), logging.INFO)

    # ── Formatters ──────────────────────────────────────────────────────
    app_formatter = logging.Formatter(fmt=log_format, datefmt=_DATEFMT)

    # ── Context filter: injects default access_context when not set ─────
    ctx_filter = _DefaultContextFilter()

    # ── Console handler ─────────────────────────────────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(app_formatter)
    console_handler.setLevel(level)
    console_handler.addFilter(ctx_filter)

    # ── Rotating file handler → app.log ────────────────────────────────
    app_file_handler = _make_rotating_handler(log_dir / "app.log", app_formatter, level)
    app_file_handler.addFilter(ctx_filter)

    # ── Root logger ─────────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(app_file_handler)

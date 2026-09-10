"""Process logging → rotating <project>/.lattice/logs/lattice.log."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from lattice.paths import lattice_home, project_root

_CONFIGURED = False

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def log_dir(home: Path | None = None) -> Path:
    return (home or lattice_home()) / "logs"


def setup_logging(
    home: Path | None = None,
    *,
    level: int = logging.INFO,
    also_stderr: bool = True,
    force: bool = False,
) -> Path:
    """Configure root logging once. Returns the active log file path."""
    global _CONFIGURED
    base = home or lattice_home()
    directory = log_dir(base)
    directory.mkdir(parents=True, exist_ok=True)
    log_file = directory / "lattice.log"

    if _CONFIGURED and not force:
        return log_file

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    if force:
        for h in list(root_logger.handlers):
            root_logger.removeHandler(h)
            h.close()

    fmt = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)

    if also_stderr:
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(logging.WARNING)
        stream.setFormatter(fmt)
        root_logger.addHandler(stream)

    logging.captureWarnings(True)
    # Library chatter (Telegram long-poll getUpdates, OpenRouter, etc.)
    for name in (
        "httpx",
        "httpcore",
        "httpx2",
        "telegram",
        "telegram.ext",
        "telegram.ext.Application",
        "openai",
        "httpcore.connection",
        "httpcore.http11",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)

    _CONFIGURED = True
    logging.getLogger("lattice").info(
        "logging to %s (project=%s, rotate 5×5MB)", log_file, project_root()
    )
    return log_file


def get_logger(name: str = "lattice") -> logging.Logger:
    return logging.getLogger(name)

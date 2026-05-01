"""
Centralized logging configuration for Flizen Backend.

Usage:
    from app.core.logging import get_logger
    logger = get_logger(__name__)

The logging behavior is controlled at startup via the --log-terminal CLI flag:
  - Always writes logs to logs/flizen.log
  - Prints to terminal only when --log-terminal is passed to uvicorn/main
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_FILE = LOG_DIR / "flizen.log"

_configured = False


def configure_logging(log_to_terminal: bool = False, log_level: int = logging.INFO) -> None:
    """
    Configure root logger with file handler (always) and optional terminal handler.

    Args:
        log_to_terminal: If True, also emit logs to stdout.
        log_level: Minimum log level (e.g. logging.DEBUG, logging.INFO).
    """
    global _configured
    if _configured:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # File handler – always active, rotates at 10 MB, keeps 5 backups
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(log_level)
    root_logger.addHandler(file_handler)

    # Terminal handler – only when explicitly requested
    if log_to_terminal:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler.setLevel(log_level)
        root_logger.addHandler(stream_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Call configure_logging() once at app startup first."""
    return logging.getLogger(name)

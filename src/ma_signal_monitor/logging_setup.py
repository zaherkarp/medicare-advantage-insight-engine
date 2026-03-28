"""Logging configuration for MA Signal Monitor."""

import logging
import sys
from pathlib import Path


def setup_logging(
    log_level: str = "INFO", log_dir: str | None = None
) -> logging.Logger:
    """Configure and return the application logger.

    Args:
        log_level: Logging level string (DEBUG, INFO, WARNING, ERROR).
        log_dir: Optional directory for log files. If None, logs to stderr only.

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger("ma_signal_monitor")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Avoid adding duplicate handlers on repeated calls
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler (stderr)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (if log_dir provided)
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path / "ma_signal_monitor.log")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger

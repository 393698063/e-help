from __future__ import annotations

import logging
import os
import sys
from typing import Final

LOGGER_NAME: Final = "e_help"
DEFAULT_LOG_FORMAT: Final = (
    "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger(LOGGER_NAME)


def configure_logging(level: str | int | None = None) -> logging.Logger:
    """Configure the application logger to write structured text to stdout.

    The function is idempotent so it is safe to call during Uvicorn reloads or
    application startup more than once.
    """
    resolved_level = level or os.getenv("LOG_LEVEL", "INFO")
    if isinstance(resolved_level, str):
        resolved_level = getattr(logging, resolved_level.upper(), logging.INFO)

    logger.setLevel(resolved_level)
    logger.propagate = False

    handler = next(
        (
            item
            for item in logger.handlers
            if getattr(item, "_e_help_console_handler", False)
        ),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler(sys.stdout)
        handler._e_help_console_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)

    handler.setLevel(resolved_level)
    handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT))
    return logger


# Make the global logger usable from any imported module without requiring
# callers to remember an extra initialization step.
configure_logging()

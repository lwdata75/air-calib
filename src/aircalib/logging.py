"""Readable, structured console logging based on ``rich``."""

from __future__ import annotations

import logging

from rich.console import Console
from rich.logging import RichHandler

console = Console(stderr=True)


def setup_logging(level: str | int = "INFO") -> None:
    """Configure the root logger with a rich handler (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level)
    if any(isinstance(h, RichHandler) for h in root.handlers):
        return
    handler = RichHandler(console=console, rich_tracebacks=True, show_path=False, log_time_format="%H:%M:%S")
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    # httpx logs every request at INFO, which drowns the pipeline's own messages.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

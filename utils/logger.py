"""
Centralised logging: rotating file handler (10MB, 5 backups) + queue handler for GUI.
All modules call get_logger(name) to get a named child logger.
LOG_QUEUE is drained by the GUI log panel every 400ms.
"""
import logging
import logging.handlers
import queue
from pathlib import Path

LOG_QUEUE: queue.Queue = queue.Queue()

_initialized = False


class _QueueHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put_nowait(self.format(record))
        except Exception:
            pass


def setup_logging(log_file: str = "bot.log", level: int = logging.INFO) -> None:
    """Configure root logger. Safe to call multiple times (idempotent)."""
    global _initialized
    if _initialized:
        return
    _initialized = True
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    # Rotating file handler — 10 MB per file, keep 5 backups
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    # Queue handler so GUI panels can drain log messages without blocking
    qh = _QueueHandler(LOG_QUEUE)
    qh.setFormatter(fmt)
    root.addHandler(qh)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

"""Line-oriented CLI diagnostics and scoped third-party logging adapters."""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import RLock
from typing import TYPE_CHECKING

from it_depends.reporting import Diagnostic, Event, Progress, Severity, emit, plain_text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import TextIO


class PlainReporter:
    """Write conventional diagnostics and task boundaries to stderr."""

    def __init__(self, level: str = "info", *, stream: TextIO | None = None) -> None:
        try:
            self.level = Severity[level.upper()]
        except KeyError as error:
            msg = f"Unknown log level {level!r}; use debug, info, warning, error, or critical"
            raise ValueError(msg) from error
        self.stream = sys.stderr if stream is None else stream
        self.lock = RLock()
        self.tasks: set[int] = set()

    def __call__(self, event: Event) -> None:
        """Render a diagnostic or task boundary without terminal controls."""
        with self.lock:
            if isinstance(event, Progress):
                self._progress(event)
            else:
                self._diagnostic(event)

    def _progress(self, event: Progress) -> None:
        if not event.finished and event.task_id in self.tasks:
            return
        self.tasks.add(event.task_id)
        state = "Stopped" if event.failed else "Finished" if event.finished else "Starting"
        self._diagnostic(Diagnostic(Severity.INFO, "it_depends", f"{state}: {event.label}"))
        if event.finished:
            self.tasks.discard(event.task_id)

    def _diagnostic(self, event: Diagnostic) -> None:
        if event.severity < self.level:
            return
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        prefix = f"{timestamp} {event.severity.name} {plain_text(event.source)}: "
        lines = plain_text(event.message).splitlines() or [""]
        if event.detail and self.level == Severity.DEBUG:
            lines.extend(plain_text(event.detail).splitlines())
        for line in lines:
            self.stream.write(f"{prefix}{line}\n")
        self.stream.flush()


class _LoggingBridge(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        severity = min(Severity, key=lambda value: abs(value - record.levelno))
        emit(Diagnostic(severity, record.name, record.getMessage()))


@contextmanager
def third_party_reporting() -> Iterator[None]:
    """Route dependency diagnostics for this CLI run without resetting root handlers."""
    handler = _LoggingBridge()
    names = ("docker", "urllib3", "httpx", "unearth")
    loggers = [logging.getLogger(name) for name in names]
    previous = [(logger.level, logger.propagate) for logger in loggers]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
    try:
        yield
    finally:
        for logger, (level, propagate) in zip(loggers, previous, strict=True):
            logger.removeHandler(handler)
            logger.setLevel(level)
            logger.propagate = propagate

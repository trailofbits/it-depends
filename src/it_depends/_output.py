"""Line-oriented CLI diagnostics and scoped third-party logging adapters."""

from __future__ import annotations

import logging
import shlex
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import RLock
from typing import TYPE_CHECKING

from loguru import logger as dependency_logger
from loguru._defaults import LOGURU_AUTOINIT

from it_depends.reporting import (
    CommandFinished,
    CommandOutput,
    CommandStarted,
    Diagnostic,
    Event,
    Progress,
    Severity,
    emit,
    plain_text,
)

_default_loguru_sink: int | None = 0 if LOGURU_AUTOINIT and sys.stderr is not None else None

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import IO

    import loguru


class PlainReporter:
    """Write conventional diagnostics and task boundaries to stderr."""

    def __init__(self, level: str = "info", *, stream: IO[str] | None = None) -> None:
        try:
            self.level = Severity[level.upper()]
        except KeyError as error:
            msg = f"Unknown log level {level!r}; use debug, info, warning, error, or critical"
            raise ValueError(msg) from error
        self.stream = sys.stderr if stream is None else stream
        self.lock = RLock()
        self.tasks: set[int] = set()
        self.commands: dict[int, str] = {}

    def start(self) -> None:
        """Begin CLI presentation; plain diagnostics require no terminal setup."""

    def stop(self) -> None:
        """End CLI presentation; plain diagnostics remain immediately available."""

    def __call__(self, event: Event) -> None:
        """Render a diagnostic or task boundary without terminal controls."""
        with self.lock:
            if isinstance(event, Progress):
                self._progress(event)
            elif isinstance(event, Diagnostic):
                self._diagnostic(event)
            elif isinstance(event, (CommandStarted, CommandOutput, CommandFinished)):
                self._command(event)

    def _command(self, event: CommandStarted | CommandOutput | CommandFinished) -> None:
        if isinstance(event, CommandStarted):
            label = shlex.join(event.argv)
            self.commands[event.command_id] = label
            message = f"Running: {label}"
        elif isinstance(event, CommandOutput):
            label = self.commands.get(event.command_id, "command")
            message = f"{label} [{event.stream}]: {event.text}"
        else:
            label = self.commands.pop(event.command_id, "command")
            message = f"Cancelled: {label}" if event.cancelled else f"{label} exited with status {event.returncode}"
            if event.returncode and not event.cancelled:
                message += "\n" + "\n".join(event.tail)
                self._diagnostic(Diagnostic(event.severity, "it_depends.command", message))
                return
        self._diagnostic(Diagnostic(Severity.INFO, "it_depends.command", message))

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
        detail = "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else ""
        emit(Diagnostic(severity, record.name, record.getMessage(), detail))


def _loguru_message(message: loguru.Message) -> None:
    record = message.record
    severity = min(Severity, key=lambda value: abs(value - record["level"].no))
    error = record["exception"]
    detail = ""
    if error is not None:
        detail = "".join(traceback.format_exception(error.type, error.value, error.traceback))
    emit(Diagnostic(severity, record["name"] or "johnnydep", record["message"], detail))


def _stderr_message(message: loguru.Message) -> None:
    sys.stderr.write(str(message))


@contextmanager
def _loguru_reporting() -> Iterator[None]:
    global _default_loguru_sink  # noqa: PLW0603 - Track only the default sink owned by Loguru.
    sink = dependency_logger.add(
        _loguru_message,
        level="DEBUG",
        filter=lambda record: (record["name"] or "").startswith("johnnydep"),
    )
    default_removed = False
    if _default_loguru_sink is not None:
        try:
            dependency_logger.remove(_default_loguru_sink)
        except ValueError:
            _default_loguru_sink = None
        else:
            default_removed = True
    try:
        yield
    finally:
        dependency_logger.remove(sink)
        if default_removed:
            _default_loguru_sink = dependency_logger.add(_stderr_message)


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
        with _loguru_reporting():
            yield
    finally:
        for logger, (level, propagate) in zip(loggers, previous, strict=True):
            logger.removeHandler(handler)
            logger.setLevel(level)
            logger.propagate = propagate

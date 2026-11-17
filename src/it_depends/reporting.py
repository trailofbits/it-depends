"""Scoped application events, independent of terminal presentation."""

from __future__ import annotations

import re
import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from enum import IntEnum
from itertools import count
from typing import TypeVar


class Severity(IntEnum):
    """Diagnostic importance, compatible with conventional log levels."""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


@dataclass(frozen=True)
class Diagnostic:
    """A diagnostic message and optional exception details."""

    severity: Severity
    source: str
    message: str
    detail: str = ""


@dataclass(frozen=True)
class Progress:
    """An immutable snapshot of one task's progress."""

    task_id: int
    label: str
    completed: int
    total: int | None
    unit: str
    parent_id: int | None = None
    finished: bool = False
    failed: bool = False


@dataclass(frozen=True)
class ResolutionProgress:
    """Discovery counts, without implying a known final graph size."""

    packages: int
    versions: int
    active: int
    queued: int


Event = Diagnostic | Progress | ResolutionProgress
Reporter = Callable[[Event], None]
_reporter: ContextVar[Reporter | None] = ContextVar("it_depends_reporter", default=None)
_task: ContextVar[int | None] = ContextVar("it_depends_task", default=None)
_identifiers = count()
_Result = TypeVar("_Result")
_CONTROL = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|"
    r"\x1b[@-_]|[\x00-\x08\x0b-\x1f\x7f-\x9f]"
)


def plain_text(value: str) -> str:
    """Remove terminal controls while preserving line breaks and tabs."""
    return _CONTROL.sub("", value)


def emit(event: Event) -> None:
    """Deliver an event to the current subscriber, if one is installed."""
    reporter = _reporter.get()
    if reporter is not None:
        reporter(event)


@contextmanager
def reporting_session(reporter: Reporter) -> Iterator[None]:
    """Subscribe to events within this context without configuring global logging."""
    token = _reporter.set(reporter)
    try:
        yield
    finally:
        _reporter.reset(token)


def in_context(function: Callable[..., _Result], *args: object) -> Callable[[], _Result]:
    """Capture this reporting context for a worker submitted to a thread pool."""
    context = copy_context()
    return lambda: context.run(function, *args)


def diagnostic(
    severity: Severity,
    source: str,
    message: object,
    *args: object,
    exception: bool = False,
) -> None:
    """Report a formatted diagnostic, preserving exception information when requested."""
    message = str(message)
    if args:
        message %= args
    detail = ""
    if exception:
        error = sys.exc_info()[1]
        if error is not None:
            message = f"{message}: {error}"
            detail = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    emit(Diagnostic(severity, source, message, detail))


class Task:
    """Report task lifecycle and progress to the current subscriber."""

    def __init__(self, label: str, *, total: int | None = None, unit: str = "steps") -> None:
        """Describe a task without starting it until its running context is entered."""
        self.task_id = next(_identifiers)
        self.label = label
        self.total = total
        self.unit = unit
        self.completed = 0
        self.parent_id = _task.get()

    def snapshot(self, *, finished: bool = False, failed: bool = False) -> Progress:
        """Return a detached snapshot suitable for another thread."""
        return Progress(
            self.task_id,
            self.label,
            self.completed,
            self.total,
            self.unit,
            self.parent_id,
            finished,
            failed,
        )

    def advance(self, amount: int = 1) -> None:
        """Record completed work."""
        self.completed += amount
        emit(self.snapshot())

    def set_total(self, total: int) -> None:
        """Update the known amount of work as discovery proceeds."""
        self.total = total
        emit(self.snapshot())

    def set_label(self, label: str) -> None:
        """Describe the current operation."""
        self.label = label
        emit(self.snapshot())

    @contextmanager
    def running(self) -> Iterator[Task]:
        """Report this task's start and finish, including failure or interruption."""
        token = _task.set(self.task_id)
        emit(self.snapshot())
        failed = True
        try:
            yield self
            failed = False
        finally:
            emit(self.snapshot(finished=True, failed=failed))
            _task.reset(token)

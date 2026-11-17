"""CLI-owned interrupt handling and terminal restoration."""

from __future__ import annotations

import os
import signal
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import FrameType

    from it_depends._commands import CommandSession
    from it_depends._output import PlainReporter


@contextmanager
def cli_interrupts(reporter: PlainReporter, commands: CommandSession) -> Iterator[None]:
    """Restore presentation on interruption; a repeated interrupt forces CLI termination."""
    interrupted = False
    previous = signal.getsignal(signal.SIGINT)

    def interrupt(_signum: int, _frame: FrameType | None) -> None:
        nonlocal interrupted
        reporter.stop()
        if interrupted:
            commands.cancel(force=True)
            sys.stderr.write("\nForced exit after interruption.\n")
            sys.stderr.flush()
            os._exit(130)
        interrupted = True
        commands.cancel()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)

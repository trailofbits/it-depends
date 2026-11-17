"""Bounded Rich presentation for interactive analysis runs."""

from __future__ import annotations

import shlex
import sys
from collections import deque
from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from it_depends._output import PlainReporter
from it_depends.reporting import (
    CommandFinished,
    CommandOutput,
    CommandStarted,
    Diagnostic,
    Event,
    Progress,
    ResolutionProgress,
    Severity,
    plain_text,
)

if TYPE_CHECKING:
    from rich.console import RenderableType

_PANEL_WIDTH = 60
_PANEL_HEIGHT = 20
_VISIBLE_COMMANDS = 2


def interactive_terminal() -> bool:
    """Require interactive input, clean stdout, and a capable stderr display."""
    console = Console(stderr=True)
    return all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)) and not (
        console.is_dumb_terminal or not console.is_terminal
    )


@dataclass
class _CommandView:
    label: str
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=20))


class RichReporter(PlainReporter):
    """Render progress in place while preserving durable diagnostics in scrollback."""

    def __init__(self, level: str = "info", *, console: Console | None = None) -> None:
        """Create one dashboard without taking terminal ownership until started."""
        self.console = console or Console(stderr=True, highlight=False, markup=False)
        super().__init__(level, stream=self.console.file)
        self.live: Live | None = None
        self.started = monotonic()
        self.progress: dict[int, Progress] = {}
        self.operations: dict[int, _CommandView] = {}
        self.recent: deque[str] = deque(maxlen=3)
        self.discovery = ResolutionProgress(0, 0, 0, 0)
        self.warnings = 0
        self.errors = 0

    def start(self) -> None:
        """Start one bounded display, preserving both original Python streams."""
        if self.level > Severity.INFO or self.live is not None:
            return
        self.live = Live(
            console=self.console,
            get_renderable=self.render,
            refresh_per_second=4,
            transient=True,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        self.live.start()

    def stop(self) -> None:
        """Restore the terminal and leave a compact discovery summary."""
        if self.live is None:
            return
        self.live.stop()
        self.live = None
        counts = self.discovery
        self.console.print(
            Text(
                f"Discovered {counts.packages} package names and {counts.versions} versions "
                f"in {monotonic() - self.started:.1f}s; "
                f"{self.warnings} warnings, {self.errors} errors."
            )
        )

    def __call__(self, event: Event) -> None:
        """Serialize state changes and preserve durable diagnostics in scrollback."""
        notice = None
        with self.lock:
            if isinstance(event, Diagnostic):
                notice = event
            elif isinstance(event, Progress):
                if event.finished:
                    self.progress.pop(event.task_id, None)
                else:
                    self.progress[event.task_id] = event
            elif isinstance(event, ResolutionProgress):
                self.discovery = event
            else:
                notice = self._operation(event)
        if notice is not None:
            self._notice(notice)

    def _notice(self, event: Diagnostic) -> None:
        with self.lock:
            if event.severity == Severity.WARNING:
                self.warnings += 1
            elif event.severity >= Severity.ERROR:
                self.errors += 1
        if event.severity < self.level:
            return
        if self.live is None:
            super()._diagnostic(event)
            return
        if event.severity < Severity.WARNING and self.level > Severity.DEBUG:
            with self.lock:
                self.recent.append(plain_text(event.message))
            return
        style = "red" if event.severity >= Severity.ERROR else "yellow" if event.severity == Severity.WARNING else ""
        self.console.print(Text(f"{event.severity.name}: {plain_text(event.message)}", style=style))
        if event.detail and self.level == Severity.DEBUG:
            self.console.print(Text(plain_text(event.detail)))

    def _operation(self, event: CommandStarted | CommandOutput | CommandFinished) -> Diagnostic | None:
        if isinstance(event, CommandStarted):
            self.operations[event.command_id] = _CommandView(plain_text(shlex.join(event.argv)))
        elif isinstance(event, CommandOutput):
            operation = self.operations.get(event.command_id)
            if operation is not None:
                operation.lines.append(plain_text(event.text))
        else:
            operation = self.operations.pop(event.command_id, _CommandView("command"))
            if event.cancelled:
                return None
            if event.returncode:
                return Diagnostic(
                    event.severity,
                    "it_depends.command",
                    f"{operation.label} exited with status {event.returncode}\n" + "\n".join(event.tail),
                )
            self.recent.append(f"Completed: {operation.label}")
        return None

    def render(self) -> Group:
        """Render a detached frame with a fixed number of visible tasks and commands."""
        with self.lock:
            counts = self.discovery
            frame: list[RenderableType] = [
                Text(f"it-depends  ·  elapsed {monotonic() - self.started:.0f}s", style="bold"),
                Text(
                    f"{counts.packages} package names · {counts.versions} versions · "
                    f"{counts.active} active · {counts.queued} queued"
                ),
            ]
            table = Table.grid(padding=(0, 1), expand=True)
            table.add_column(ratio=3, overflow="ellipsis", no_wrap=True)
            table.add_column(ratio=1)
            task_limit = min(4, max(1, self.console.height // 5))
            for task in list(self.progress.values())[-task_limit:]:
                label = Text(plain_text(task.label))
                if task.total is None:
                    table.add_row(label, Spinner("dots"))
                else:
                    table.add_row(
                        label,
                        Group(
                            ProgressBar(total=task.total, completed=task.completed),
                            Text(f"{task.completed}/{task.total} {task.unit}"),
                        ),
                    )
            frame.append(table)
            self._command_panels(frame)
            frame.append(Text(f"{self.warnings} warnings · {self.errors} errors"))
            return Group(*frame)

    def _command_panels(self, frame: list[RenderableType]) -> None:
        roomy = self.console.width >= _PANEL_WIDTH and self.console.height >= _PANEL_HEIGHT
        visible = min(_VISIBLE_COMMANDS, max(1, (self.console.height - 13) // 5))
        for command in list(self.operations.values())[:visible]:
            text = Text("\n".join(list(command.lines)[-3:]) or "Waiting for output…")
            if roomy:
                frame.append(Panel(text, title=Text(command.label), height=5))
            else:
                frame.append(Text(command.label, overflow="ellipsis", no_wrap=True))
        if len(self.operations) > visible:
            frame.append(Text(f"… {len(self.operations) - visible} additional commands running"))
        if self.recent:
            frame.append(Text(self.recent[-1], style="dim", overflow="ellipsis", no_wrap=True))


def make_reporter(level: str) -> PlainReporter:
    """Select live presentation only when every required stream supports interaction."""
    return RichReporter(level) if interactive_terminal() else PlainReporter(level)

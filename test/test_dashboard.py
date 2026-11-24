"""Terminal capability checks and readable dashboard behavior."""

import io
import itertools
import sys

import pytest
from rich.console import Console

from it_depends._dashboard import RichReporter, interactive_terminal
from it_depends.reporting import (
    CommandFinished,
    CommandOutput,
    CommandStarted,
    Diagnostic,
    ResolutionProgress,
    Severity,
)


@pytest.mark.parametrize(("stdin", "stdout", "stderr"), list(itertools.product([False, True], repeat=3)))
def test_live_requires_all_three_terminal_streams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdin: bool,
    stdout: bool,
    stderr: bool,
) -> None:
    for name, enabled in (("stdin", stdin), ("stdout", stdout), ("stderr", stderr)):
        monkeypatch.setattr(getattr(sys, name), "isatty", lambda value=enabled: value)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert interactive_terminal() is (stdin and stdout and stderr)


def test_dumb_terminal_disables_live(monkeypatch: pytest.MonkeyPatch) -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        monkeypatch.setattr(stream, "isatty", lambda: True)
    monkeypatch.setenv("TERM", "dumb")
    assert not interactive_terminal()


@pytest.mark.parametrize("width", [40, 80, 120])
def test_dashboard_shows_counts_and_literal_bounded_command_output(width: int) -> None:
    stream = io.StringIO()
    console = Console(file=stream, width=width, height=24, color_system=None)
    reporter = RichReporter(console=console)
    reporter(ResolutionProgress(12, 48, 2, 7))
    reporter(CommandStarted(1, ("pip", "wheel", "demo")))
    for number in range(100):
        reporter(CommandOutput(1, "stderr", f"line {number} [red]literal[/red]\x1b[31m"))
    console.print(reporter.render())
    rendered = stream.getvalue()
    assert "12 package names" in rendered
    assert "pip wheel demo" in rendered
    wide_layout = 80
    if width >= wide_layout:
        assert "line 99 [red]literal[/red]" in rendered
        assert "line 0 " not in rendered
    assert "\x1b" not in rendered


def test_failures_remain_visible_and_successful_commands_collapse() -> None:
    stream = io.StringIO()
    reporter = RichReporter(console=Console(file=stream, width=100))
    reporter(CommandStarted(1, ("npm", "view", "demo")))
    reporter(CommandFinished(1, 1, ("registry unavailable",)))
    reporter(CommandStarted(2, ("cargo", "metadata")))
    reporter(CommandOutput(2, "stderr", "temporary progress"))
    reporter(CommandFinished(2, 0))
    reporter(CommandStarted(3, ("git", "ls-remote", "unavailable-scheme")))
    reporter(CommandFinished(3, 1, severity=Severity.DEBUG))
    reporter.console.print(reporter.render())
    rendered = stream.getvalue()
    assert "registry unavailable" in rendered
    assert "Completed: cargo metadata" in rendered
    assert "temporary progress" not in rendered
    assert "1 errors" in rendered


def test_warning_and_summary_survive_display_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "xterm-256color")
    stream = io.StringIO()
    console = Console(
        file=stream,
        width=100,
        height=24,
        force_terminal=True,
        force_interactive=True,
        color_system=None,
    )
    reporter = RichReporter(console=console)
    reporter.start()
    reporter(ResolutionProgress(4, 20, 0, 0))
    reporter(Diagnostic(Severity.INFO, "resolver", "Current metadata activity"))
    reporter(Diagnostic(Severity.WARNING, "resolver", "Could not resolve optional package"))
    reporter.stop()
    rendered = stream.getvalue()
    assert "Could not resolve optional package" in rendered
    assert "INFO:" not in rendered
    assert "Discovered 4 package names and 20 versions" in rendered
    assert "\x1b[?25l" in rendered
    assert "\x1b[?25h" in rendered

"""Exercise real terminal streams and interruption without registry access."""

import errno
import json
import os
import select
import signal
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from time import monotonic, sleep

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Requires POSIX pseudo-terminals")

_SCRIPT = """
import sys
import time
from unittest.mock import MagicMock
from it_depends import _cli
from it_depends.models import Package
from it_depends.reporting import Task, ResolutionProgress, emit

def resolve(*args, **kwargs):
    packages = kwargs['repo']
    packages.add(Package('demo', '1.0.0', 'pip'))
    with Task('Interruptible analysis').running():
        emit(ResolutionProgress(1, 1, 0, 0))
        if MODE == 'interrupt':
            time.sleep(60)
        if MODE == 'failure':
            raise ValueError('test resolution failed')
    return packages

_cli.resolve = resolve
_cli.DBPackageCache = MagicMock()
sys.argv = ['it-depends', 'pip:demo', '--trust', '--database', ':memory:']
_cli.main()
"""


@contextmanager
def terminals() -> Iterator[tuple[tuple[int, int], ...]]:
    """Create independent input, result, and diagnostic terminals."""
    pairs = tuple(os.openpty() for _ in range(3))
    try:
        yield pairs
    finally:
        for pair in pairs:
            for descriptor in pair:
                os.close(descriptor)


def read_available(descriptor: int) -> bytes:
    try:
        return os.read(descriptor, 65536)
    except OSError as error:
        if error.errno != errno.EIO:
            raise
        return b""


def read_until(descriptor: int, expected: bytes) -> bytes:
    result = bytearray()
    deadline = monotonic() + 10
    while expected not in result and monotonic() < deadline:
        if select.select([descriptor], [], [], 0.1)[0]:
            result.extend(read_available(descriptor))
    assert expected in result, result.decode(errors="replace")
    return bytes(result)


def remaining(descriptor: int) -> bytes:
    result = bytearray()
    while select.select([descriptor], [], [], 0.1)[0]:
        chunk = read_available(descriptor)
        if not chunk:
            break
        result.extend(chunk)
    return bytes(result)


def finish_process(process: subprocess.Popen[bytes], descriptor: int) -> tuple[int, bytes]:
    output = bytearray()
    deadline = monotonic() + 10
    while process.poll() is None and monotonic() < deadline:
        if select.select([descriptor], [], [], 0.1)[0]:
            output.extend(read_available(descriptor))
    return process.wait(timeout=1), bytes(output) + remaining(descriptor)


@pytest.mark.parametrize("mode", ["success", "failure"])
def test_cli_restores_cursor_and_keeps_stdout_clean(mode: str) -> None:
    with terminals() as ((_, stdin), (output, stdout), (diagnostics, stderr)):
        result = subprocess.run(
            [sys.executable, "-c", f"MODE = {mode!r}\n" + _SCRIPT],
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            env=os.environ | {"TERM": "xterm-256color", "NO_COLOR": "1"},
            check=False,
            timeout=10,
        )
        rendered = remaining(diagnostics)
        assert b"\x1b[?25l" in rendered
        assert b"\x1b[?25h" in rendered
        if mode == "success":
            assert result.returncode == 0
            assert "pip:demo" in json.loads(remaining(output))
        else:
            assert result.returncode != 0
            assert b"test resolution failed" in rendered
            assert remaining(output) == b""


def test_redirected_stdout_disables_all_cursor_controls() -> None:
    with terminals() as ((_, stdin), (_, _stdout), (diagnostics, stderr)):
        result = subprocess.run(
            [sys.executable, "-c", "MODE = 'success'\n" + _SCRIPT],
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=stderr,
            env=os.environ | {"TERM": "xterm-256color"},
            check=True,
            timeout=10,
        )
        assert "pip:demo" in json.loads(result.stdout)
        assert b"\x1b" not in remaining(diagnostics)


@pytest.mark.parametrize("answer", [b"y\n", b"n\n", None])
def test_interrupt_exports_only_approved_partial_results(answer: bytes | None) -> None:
    with terminals() as ((input_master, stdin), (output, stdout), (diagnostics, stderr)):
        process = subprocess.Popen(
            [sys.executable, "-c", "MODE = 'interrupt'\n" + _SCRIPT],
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            env=os.environ | {"TERM": "xterm-256color"},
        )
        try:
            rendered = read_until(diagnostics, b"Interruptible analysis")
            process.send_signal(signal.SIGINT)
            rendered += read_until(diagnostics, b"partial results?")
            if answer is None:
                process.send_signal(signal.SIGINT)
            else:
                os.write(input_master, answer)
            expected_status = 130
            assert process.wait(timeout=5) == expected_status
            rendered += remaining(diagnostics)
            assert b"\x1b[?25h" in rendered
            artifact = remaining(output)
            if answer == b"y\n":
                assert "pip:demo" in json.loads(artifact)
                assert b"exported partial results" in rendered
            else:
                assert artifact == b""
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def test_saved_explorer_restores_screen_and_leaves_stdout_empty(tmp_path: Path) -> None:
    path = tmp_path / "saved.json"
    path.write_text("{}")
    with terminals() as ((input_master, stdin), (output, stdout), (diagnostics, stderr)):
        process = subprocess.Popen(
            [sys.executable, "-m", "it_depends", "--explore", str(path)],
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            env=os.environ | {"TERM": "xterm-256color", "NO_COLOR": "1"},
        )
        try:
            rendered = read_until(diagnostics, b"saved components")
            os.write(input_master, b"q")
            status, final_output = finish_process(process, diagnostics)
            assert status == 0
            rendered += final_output
            assert b"\x1b[?1049h" in rendered
            assert b"\x1b[?1049l" in rendered
            assert remaining(output) == b""
            assert path.read_text() == "{}"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def test_headless_interruption_never_prompts_or_emits_partial_json() -> None:
    with terminals() as ((_, stdin), (_, _stdout), (diagnostics, stderr)):
        process = subprocess.Popen(
            [sys.executable, "-c", "MODE = 'interrupt'\n" + _SCRIPT],
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=stderr,
            env=os.environ | {"TERM": "xterm-256color"},
        )
        try:
            rendered = read_until(diagnostics, b"Interruptible analysis")
            process.send_signal(signal.SIGINT)
            expected_status = 130
            assert process.wait(timeout=5) == expected_status
            rendered += remaining(diagnostics)
            assert b"partial results?" not in rendered
            assert process.stdout.read() == b""
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


_COMMAND_INTERRUPT_SCRIPT = """
import sys
from pathlib import Path
from it_depends._commands import CommandSession, run_command
from it_depends._interrupts import cli_interrupts
from it_depends._output import PlainReporter

child = (
    "import os, signal, sys, time\\n"
    "from pathlib import Path\\n"
    "directory = Path(sys.argv[1])\\n"
    "def terminate(*_args): (directory / 'terminated').touch()\\n"
    "signal.signal(signal.SIGTERM, terminate)\\n"
    "time.sleep(0.2)\\n"
    "(directory / 'ready').write_text(str(os.getpid()))\\n"
    "time.sleep(60)\\n"
)
with CommandSession().running() as commands, cli_interrupts(PlainReporter(), commands):
    run_command([sys.executable, '-c', child, sys.argv[1]])
"""


def test_second_interrupt_forces_exit_during_command_cleanup(tmp_path: Path) -> None:
    with subprocess.Popen(
        [sys.executable, "-c", _COMMAND_INTERRUPT_SCRIPT, str(tmp_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as process:
        try:
            for name in ("ready", "terminated"):
                marker = tmp_path / name
                deadline = monotonic() + 5
                while not marker.exists() and monotonic() < deadline:
                    sleep(0.01)
                assert marker.exists()
                process.send_signal(signal.SIGINT)
            expected_status = 130
            assert process.wait(timeout=3) == expected_status
        finally:
            if process.poll() is None:
                ready = tmp_path / "ready"
                if ready.exists():
                    with suppress(ProcessLookupError):
                        os.killpg(int(ready.read_text()), signal.SIGKILL)
                process.kill()
                process.wait()

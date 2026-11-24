"""Stream subprocess diagnostics without mixing them with machine-readable results."""

from __future__ import annotations

import codecs
import os
import re
import signal
import subprocess
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from itertools import count
from threading import Lock, RLock, current_thread, main_thread
from typing import TYPE_CHECKING

from it_depends.reporting import CommandFinished, CommandOutput, CommandStarted, Severity, emit, in_context

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from io import BufferedReader
    from pathlib import Path
    from typing import IO

_ids = count()
_session: ContextVar[CommandSession | None] = ContextVar("it_depends_commands", default=None)
_LINE_LIMIT = 8192


_URL_USERINFO = re.compile(r"([a-z][a-z0-9+.-]*://)([^/@?#\s]+)@", re.IGNORECASE)


def _reporting_arguments(argv: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    display: list[str] = []
    secrets: set[str] = set()
    previous = ""
    for arg in argv:
        label = _URL_USERINFO.sub(r"\1<redacted>@", arg)
        for _, userinfo in _URL_USERINFO.findall(arg):
            secrets.add(userinfo)
            _, _, password = userinfo.partition(":")
            if password:
                secrets.add(password)
        if previous in {"-e", "--env"} or arg.startswith("--env="):
            _, _, value = arg.removeprefix("--env=").partition("=")
            if value:
                secrets.add(value)
                label = arg[: -len(value)] + "<redacted>"
        display.append(label)
        previous = arg
    return tuple(display), tuple(sorted(secrets, key=len, reverse=True))


class CommandCapture:
    """Report command output while keeping only a bounded diagnostic tail."""

    def __init__(self, argv: Sequence[str]) -> None:
        """Assign an operation identifier and retain its command line."""
        self.command_id = next(_ids)
        self.argv, self._secrets = _reporting_arguments(argv)
        self.tail: deque[str] = deque(maxlen=20)
        self.lock = RLock()

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "<redacted>")
        return text

    def write(self, text: str, stream: str = "stdout") -> None:
        """Report literal diagnostic text with bounded line lengths."""
        with self.lock:
            for part in self._redact(text).replace("\r", "\n").splitlines():
                line = part[-_LINE_LIMIT:]
                self.tail.append(line)
                emit(CommandOutput(self.command_id, stream, line))

    @contextmanager
    def running(self) -> Iterator[CommandCapture]:
        """Report lifecycle for an external API operation, such as a Docker build."""
        emit(CommandStarted(self.command_id, self.argv))
        status = 1
        try:
            yield self
            status = 0
        finally:
            session = _session.get()
            emit(
                CommandFinished(
                    self.command_id,
                    status,
                    tuple(self.tail),
                    cancelled=session is not None and session.cancelled,
                )
            )


def _signal(process: subprocess.Popen[bytes], *, force: bool) -> bool:
    """Signal an owned process group without waiting for termination."""
    if os.name != "posix" and process.poll() is not None:
        return False
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        elif force:
            process.kill()
        else:
            process.terminate()
    except ProcessLookupError:
        return False
    return True


def _stop(process: subprocess.Popen[bytes], *, force: bool = False, wait: bool = True) -> None:
    signalled = _signal(process, force=force)
    if not wait:
        return
    if signalled:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            force = True
        if os.name == "posix" or force:
            _signal(process, force=True)
    process.wait()


class CommandSession:
    """Track only subprocesses owned by one analysis run."""

    def __init__(self) -> None:
        """Initialize an uncancelled operation registry."""
        self.processes: set[subprocess.Popen[bytes]] = set()
        self.cancelled = False
        self.lock = RLock()
        self._cleanup_lock = Lock()

    def cancel(self, *, force: bool = False, wait: bool = True) -> None:
        """Stop active subprocess groups and prevent subsequent commands.

        Args:
            force: Kill processes immediately instead of requesting termination.
            wait: Wait for termination and reap the child processes.

        """
        with self._cleanup_lock if wait else nullcontext():
            with self.lock:
                self.cancelled = True
                processes = tuple(self.processes)
            for process in processes:
                _stop(process, force=force, wait=wait)
                if wait:
                    with self.lock:
                        self.processes.discard(process)

    @contextmanager
    def running(self) -> Iterator[CommandSession]:
        """Bind this registry to the current analysis and its captured worker contexts."""
        token = _session.set(self)
        try:
            yield self
        finally:
            self.cancel()
            _session.reset(token)

    @contextmanager
    def launch(self, start: Callable[[], subprocess.Popen[bytes]]) -> Iterator[subprocess.Popen[bytes]]:
        """Start and register a child atomically with respect to cancellation."""
        with self.lock:
            if self.cancelled:
                raise KeyboardInterrupt
            process = start()
            self.processes.add(process)
        try:
            with process:
                yield process
        finally:
            with self.lock:
                self.processes.discard(process)


@contextmanager
def resolution_commands() -> Iterator[None]:
    """Bind worker commands to this resolution and cancel them before the pool joins."""
    session = _session.get()
    if session is None:
        with CommandSession().running():
            yield
        return
    try:
        yield
    except BaseException:
        session.cancel()
        raise


def cancel_commands() -> None:
    """Cancel subprocesses in the current analysis session, if one is active."""
    session = _session.get()
    if session is not None:
        session.cancel()


def _read(
    pipe: BufferedReader,
    capture: CommandCapture,
    stream: str,
    *,
    display: bool,
    retain: bool,
) -> bytes:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    data = bytearray()
    pending = ""
    with pipe:
        while chunk := pipe.read1(4096):
            if retain:
                data.extend(chunk)
            if display:
                pending += decoder.decode(chunk).replace("\r", "\n")
                lines = pending.split("\n")
                pending = lines.pop()
                for line in lines:
                    capture.write(line, stream)
                if len(pending) >= _LINE_LIMIT:
                    capture.write(pending, stream)
                    pending = ""
        if display:
            capture.write(pending + decoder.decode(b"", final=True), stream)
    return bytes(data)


def _collect(
    process: subprocess.Popen[bytes],
    capture: CommandCapture,
    *,
    capture_stdout: bool,
    capture_stderr: bool,
) -> subprocess.CompletedProcess[bytes]:
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="it-depends-output") as pool:
        try:
            readers = {}
            for name, pipe, retain in (
                ("stdout", process.stdout, capture_stdout),
                ("stderr", process.stderr, capture_stderr),
            ):
                if pipe is not None:
                    function = in_context(
                        lambda p=pipe, n=name, r=retain: _read(
                            p,
                            capture,
                            n,
                            display=n == "stderr" or not capture_stdout,
                            retain=r,
                        )
                    )
                    readers[name] = pool.submit(function)
            returncode = process.wait()
            output = {name: reader.result() for name, reader in readers.items()}
        except BaseException:
            _stop(process)
            raise
    return subprocess.CompletedProcess(
        capture.argv,
        returncode,
        output.get("stdout"),
        output.get("stderr"),
    )


def run_command(  # noqa: PLR0913 - Preserve explicit subprocess stream controls.
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    check: bool = True,
    capture_stdout: bool = False,
    capture_output: bool = False,
    stdin: int | IO[bytes] | None = subprocess.DEVNULL,
    stdout: int | IO[bytes] | None = None,
    stderr: int | IO[bytes] | None = None,
    env: dict[str, str] | None = None,
    failure_severity: Severity = Severity.ERROR,
) -> subprocess.CompletedProcess[bytes]:
    """Execute a command, preserving captured bytes and streaming only human diagnostics.

    Args:
        argv: Executable and arguments; no shell interpretation is performed.
        cwd: Working directory.
        check: Raise CalledProcessError when the command fails.
        capture_stdout: Retain stdout as machine-readable bytes without displaying it.
        capture_output: Retain both output streams, also displaying stderr diagnostics.
        stdin: Input descriptor; analysis commands receive no interactive input by default.
        stdout: Explicit output descriptor, preserved without interception.
        stderr: Explicit diagnostic descriptor, preserved without interception.
        env: Optional replacement environment.
        failure_severity: Diagnostic importance of nonzero exits, including expected probes.

    Returns:
        The process result, including streams requested for capture.

    """
    session = _session.get() or CommandSession()
    if session.cancelled:
        raise KeyboardInterrupt
    capture = CommandCapture(argv)
    emit(CommandStarted(capture.command_id, capture.argv))

    def start() -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=stdin,
            stdout=subprocess.PIPE if stdout is None else stdout,
            stderr=subprocess.PIPE if stderr is None else stderr,
            env=env,
            start_new_session=os.name == "posix",
        )

    def execute() -> subprocess.CompletedProcess[bytes]:
        with session.launch(start) as process:
            return _collect(
                process,
                capture,
                capture_stdout=capture_stdout or capture_output or stdout == subprocess.PIPE,
                capture_stderr=capture_output or stderr == subprocess.PIPE,
            )

    status = 1
    try:
        if current_thread() is main_thread():
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="it-depends-command") as pool:
                try:
                    result = pool.submit(in_context(execute)).result()
                except KeyboardInterrupt:
                    session.cancel()
                    raise
        else:
            result = execute()
        status = result.returncode
        if check:
            result.check_returncode()
        return result
    finally:
        emit(
            CommandFinished(
                capture.command_id,
                status,
                tuple(capture.tail),
                cancelled=session.cancelled,
                severity=failure_severity,
            )
        )

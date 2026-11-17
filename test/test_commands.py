"""Local subprocess behavior at the command reporting boundary."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event as ThreadEvent
from unittest.mock import Mock, patch

import pytest
from docker.errors import DockerException

from it_depends._commands import CommandSession, run_command
from it_depends.docker import DockerContainer, InMemoryDockerfile
from it_depends.reporting import (
    CommandFinished,
    CommandOutput,
    Event,
    in_context,
    reporting_session,
)


def test_machine_stdout_is_preserved_and_stderr_is_streamed() -> None:
    events: list[Event] = []
    script = "import os; os.write(1, b'\\x00\\xff{\\\"value\\\":1}'); os.write(2,b'progress\\rnext\\n')"
    with reporting_session(events.append):
        result = run_command([sys.executable, "-c", script], capture_stdout=True)
    assert result.stdout == b'\x00\xff{"value":1}'
    assert [event.text for event in events if isinstance(event, CommandOutput)] == ["progress", "next"]


def test_both_streams_are_drained_without_deadlock() -> None:
    script = "import os; os.write(1,b'x'*200000); os.write(2,b'y'*200000)"
    result = run_command([sys.executable, "-c", script], capture_output=True)
    assert result.stdout == b"x" * 200000
    assert result.stderr == b"y" * 200000


def test_output_is_available_before_command_exits() -> None:
    ready = ThreadEvent()

    def observe(event: Event) -> None:
        if isinstance(event, CommandOutput):
            ready.set()

    session = CommandSession()
    script = "import time; print('ready', flush=True); time.sleep(60)"
    with reporting_session(observe), session.running(), ThreadPoolExecutor() as pool:
        future = pool.submit(in_context(run_command, [sys.executable, "-c", script]))
        assert ready.wait(timeout=5)
        assert not future.done()
        session.cancel()
        with pytest.raises(subprocess.CalledProcessError):
            future.result(timeout=5)


def test_failure_retains_useful_tail_and_exit_status() -> None:
    events: list[Event] = []
    script = "import sys; print('failure detail', file=sys.stderr); sys.exit(7)"
    with reporting_session(events.append), pytest.raises(subprocess.CalledProcessError) as raised:
        run_command([sys.executable, "-c", script])
    expected_status = 7
    assert raised.value.returncode == expected_status
    finished = [event for event in events if isinstance(event, CommandFinished)]
    assert finished[-1].tail == ("failure detail",)


def test_explicit_descriptors_are_not_intercepted(tmp_path: Path) -> None:
    output = tmp_path / "output"
    events: list[Event] = []
    with output.open("wb") as stream, reporting_session(events.append):
        run_command([sys.executable, "-c", "print('machine data')"], stdout=stream)
    assert output.read_bytes() == b"machine data\n"
    assert not [event for event in events if isinstance(event, CommandOutput)]


def test_cancellation_prevents_new_commands() -> None:
    with CommandSession().running() as session:
        session.cancel()
        with pytest.raises(KeyboardInterrupt):
            run_command([sys.executable, "-c", "raise RuntimeError('must not execute')"])


def test_incomplete_utf8_and_long_lines_are_bounded() -> None:
    events: list[Event] = []
    script = "import os; os.write(2,b'x'*20000+b'\\xff')"
    with reporting_session(events.append):
        run_command([sys.executable, "-c", script])
    lines = [event.text for event in events if isinstance(event, CommandOutput)]
    line_limit = 8192
    assert all(len(line) <= line_limit for line in lines)
    assert lines[-1].endswith("\ufffd")


def test_process_start_failure_is_reported(tmp_path: Path) -> None:
    events: list[Event] = []
    with reporting_session(events.append), pytest.raises(FileNotFoundError):
        run_command([str(tmp_path / "missing")])
    assert isinstance(events[-1], CommandFinished)
    assert events[-1].returncode != 0


@pytest.mark.skipif(os.name != "posix", reason="Requires process groups")
def test_cancellation_stops_descendants_that_ignore_termination() -> None:
    ready = ThreadEvent()

    def observe(event: Event) -> None:
        if isinstance(event, CommandOutput) and event.text == "ready":
            ready.set()

    script = (
        "import os, signal, time\n"
        "if os.fork() == 0:\n"
        " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        " print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    session = CommandSession()
    with reporting_session(observe), ThreadPoolExecutor() as pool, session.running():
        future = pool.submit(in_context(run_command, [sys.executable, "-c", script]))
        assert ready.wait(timeout=5)
        session.cancel()
        with pytest.raises(subprocess.CalledProcessError):
            future.result(timeout=5)


def test_docker_build_error_cannot_be_reported_as_success() -> None:
    client = Mock()
    client.build.return_value = [b'{"error":"invalid build step"}\n']
    events: list[Event] = []
    with (
        InMemoryDockerfile("FROM scratch\n") as dockerfile,
        patch("it_depends.docker._discover_podman_socket", return_value="unix:///unused"),
        patch("it_depends.docker.docker.APIClient", return_value=client),
        reporting_session(events.append),
    ):
        container = DockerContainer("example/image", dockerfile)
        with pytest.raises(DockerException, match="invalid build step"):
            container.rebuild()
    finished = [event for event in events if isinstance(event, CommandFinished)]
    assert finished[-1].returncode != 0

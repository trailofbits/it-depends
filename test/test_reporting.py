"""Behavior of scoped reporting and plain CLI output."""

import io
import json
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from loguru import logger as dependency_logger

from it_depends import _cli
from it_depends._output import PlainReporter, third_party_reporting
from it_depends.cache import PackageRepository
from it_depends.docker import DockerContainer
from it_depends.models import Package
from it_depends.reporting import (
    Diagnostic,
    Event,
    Progress,
    Severity,
    Task,
    diagnostic,
    emit,
    in_context,
    plain_text,
    reporting_session,
)


def test_nested_sessions_and_workers_are_isolated() -> None:
    outer: list[Event] = []
    inner: list[Event] = []
    message = Diagnostic(Severity.WARNING, "resolver", "missing package")
    with reporting_session(outer.append):
        with reporting_session(inner.append), ThreadPoolExecutor() as pool:
            pool.submit(in_context(emit, message)).result()
        emit(message)
    emit(message)
    assert outer == inner == [message]


def test_tasks_report_nested_lifecycle_and_failure() -> None:
    events: list[Event] = []
    parent = Task("resolve")

    def fail() -> None:
        with reporting_session(events.append), parent.running(), Task("build", total=2).running() as child:
            child.advance()
            child.set_total(3)
            msg = "failed"
            raise ValueError(msg)

    with pytest.raises(ValueError, match="failed"):
        fail()
    progress = [event for event in events if isinstance(event, Progress)]
    assert progress[1].parent_id == parent.task_id
    assert progress[-2].finished
    assert progress[-2].failed
    assert progress[-1].finished
    assert progress[-1].failed
    assert progress[1].completed == 0


def test_plain_diagnostics_filter_and_sanitize_external_content() -> None:
    stream = io.StringIO()
    reporter = PlainReporter("warning", stream=stream)
    reporter(Diagnostic(Severity.INFO, "resolver", "hidden"))
    reporter(Diagnostic(Severity.ERROR, "resolver", "\x1b[31m[red]literal[/red]\x1b[0m\nnext"))
    assert "hidden" not in stream.getvalue()
    assert "ERROR resolver: [red]literal[/red]" in stream.getvalue()
    assert "ERROR resolver: next" in stream.getvalue()
    assert "\x1b" not in stream.getvalue()


@pytest.mark.parametrize("on_finish", [False, True])
def test_subscriber_failure_restores_parent_task(*, on_finish: bool) -> None:
    def fail(event: Event) -> None:
        if isinstance(event, Progress) and event.finished == on_finish:
            msg = "output closed"
            raise OSError(msg)

    with Task("outer").running() as outer:
        with reporting_session(fail), pytest.raises(OSError, match="output closed"), Task("inner").running():
            pass
        assert Task("sibling").parent_id == outer.task_id


def test_exception_detail_is_available_to_subscribers() -> None:
    events: list[Event] = []
    error = ValueError("broken input")

    def fail() -> None:
        raise error

    with reporting_session(events.append):
        try:
            fail()
        except ValueError:
            diagnostic(Severity.ERROR, "resolver", "Cannot resolve %s", "demo", exception=True)
    event = events[0]
    assert isinstance(event, Diagnostic)
    assert event.message == "Cannot resolve demo: broken input"
    assert "ValueError: broken input" in event.detail


def test_invalid_level_is_actionable() -> None:
    with pytest.raises(ValueError, match="Unknown log level"):
        PlainReporter("invalid")


def test_plain_task_updates_do_not_flood_logs() -> None:
    stream = io.StringIO()
    with reporting_session(PlainReporter(stream=stream)), Task("resolve").running() as task:
        for _ in range(100):
            task.advance()
    assert [line.partition("it_depends: ")[2] for line in stream.getvalue().splitlines()] == [
        "Starting: resolve",
        "Finished: resolve",
    ]


def test_control_removal_preserves_text() -> None:
    assert plain_text("\x1b]0;title\x07hello\rworld\x00\n") == "helloworld\n"


def test_third_party_logging_preserves_root_and_restores_namespace_settings() -> None:
    events: list[Event] = []
    root_handlers = logging.getLogger().handlers[:]
    logger = logging.getLogger("docker")
    previous = (logger.level, logger.propagate, logger.handlers[:])
    with reporting_session(events.append), third_party_reporting():
        logger.warning("container unavailable")
    assert logging.getLogger().handlers == root_handlers
    assert (logger.level, logger.propagate, logger.handlers) == previous
    assert any(isinstance(event, Diagnostic) and event.message == "container unavailable" for event in events)


def test_repeated_cli_sessions_capture_johnnydep_without_duplicate_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    events: list[Event] = []
    logger = dependency_logger.patch(lambda record: record.update(name="johnnydep.tests"))
    for _ in range(2):
        with reporting_session(events.append), third_party_reporting():
            logger.warning("resolver diagnostic")
    assert [event.message for event in events if isinstance(event, Diagnostic)] == [
        "resolver diagnostic",
        "resolver diagnostic",
    ]
    assert capsys.readouterr().err == ""


def test_logging_bridge_preserves_exception_traceback() -> None:
    events: list[Event] = []
    error = OSError("socket closed")
    with reporting_session(events.append), third_party_reporting():
        try:
            raise error
        except OSError:
            logging.getLogger("docker").exception("connection failed")
    (event,) = events
    assert isinstance(event, Diagnostic)
    assert event.message == "connection failed"
    assert "Traceback (most recent call last)" in event.detail
    assert "OSError: socket closed" in event.detail


def test_cli_keeps_custom_loguru_sink_when_autoinit_is_disabled() -> None:
    script = """
from loguru import logger
messages = []
sink = logger.add(messages.append, format="{message}")
from it_depends._output import third_party_reporting
with third_party_reporting():
    logger.warning("during")
logger.warning("after")
logger.remove(sink)
assert [message.strip() for message in messages] == ["during", "after"]
"""
    subprocess.run([sys.executable, "-c", script], check=True, env=os.environ | {"LOGURU_AUTOINIT": "false"})


def test_queued_loguru_default_preserves_host_output_and_scoped_diagnostics() -> None:
    script = """
from concurrent.futures import ThreadPoolExecutor
from loguru import logger
from it_depends._output import third_party_reporting
from it_depends.reporting import reporting_session

events = []
johnnydep = logger.patch(lambda record: record.update(name="johnnydep.lib"))
with reporting_session(events.append), third_party_reporting(), ThreadPoolExecutor() as pool:
    johnnydep.warning("from resolver")
    pool.submit(logger.warning, "from host").result()
    pool.submit(johnnydep.warning, "from another analysis").result()
assert [e.message for e in events] == ["from resolver"]
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        env=os.environ | {"LOGURU_AUTOINIT": "true", "LOGURU_ENQUEUE": "true"},
        capture_output=True,
        text=True,
    )
    assert "from resolver" not in result.stderr
    assert result.stderr.count("from host") == 1
    assert result.stderr.count("from another analysis") == 1


@pytest.mark.parametrize(
    ("level", "threshold"), [("WARN", Severity.WARNING), ("fatal", Severity.CRITICAL), ("NOTSET", Severity.DEBUG)]
)
def test_standard_log_level_aliases(level: str, threshold: Severity) -> None:
    assert PlainReporter(level).level == threshold


def test_cold_docker_pull_keeps_cli_stdout_parseable(
    capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    docker_cli = tmp_path / "docker"
    docker_cli.write_text("#!/bin/sh\necho pulling-image\n")
    docker_cli.chmod(0o755)
    container = DockerContainer("example", tag="v1")
    client = MagicMock()
    client.images.list.side_effect = [[], [MagicMock(tags=[container.name])]]
    monkeypatch.setattr("it_depends.docker.docker.from_env", lambda: client)
    monkeypatch.setattr("it_depends.docker.resolve_executable", lambda _tool: str(docker_cli))

    def resolve(*_args: object, **_kwargs: object) -> PackageRepository:
        assert container.exists() is None
        with redirect_stderr(io.StringIO()):
            container.pull()
        repo = PackageRepository()
        repo.add(Package("example", "1.0.0", "pip"))
        return repo

    monkeypatch.setattr(_cli, "resolve", resolve)
    monkeypatch.setattr(_cli, "DBPackageCache", MagicMock())
    monkeypatch.setattr(sys, "argv", ["it-depends", "--trust", "--log-level", "info", "pip:example"])
    _cli.main()
    stdout, stderr = capfd.readouterr()
    assert "pip:example" in json.loads(stdout)
    assert "pulling-image" in stderr

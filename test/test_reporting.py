"""Behavior of scoped reporting and plain CLI output."""

import io
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
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

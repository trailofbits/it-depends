"""Behavior of scoped reporting and plain CLI output."""

import io
from concurrent.futures import ThreadPoolExecutor

import pytest

from it_depends._output import PlainReporter
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

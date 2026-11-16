import json
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from prompt_toolkit.input import PipeInput, create_pipe_input
from prompt_toolkit.output import DummyOutput

from it_depends import trust
from it_depends.models import Dependency
from it_depends.trust import TrustError, require_trust

pytestmark = pytest.mark.usefixtures("terminal", "trust_file")


@pytest.fixture
def keyboard() -> Iterator[PipeInput]:
    with (
        create_pipe_input() as pipe,
        patch.object(trust, "create_input", return_value=pipe),
        patch.object(trust, "create_output", return_value=DummyOutput()),
    ):
        yield pipe


@pytest.mark.parametrize(
    ("keys", "remembered"),
    [("\x1b[A\r", False), ("\x1b[A\x1b[A\r", True), ("\x1b[A\x1b[A\x1b[B\r", False)],
)
def test_arrow_keys_select_trust_duration(
    keyboard: PipeInput, trust_file: Path, keys: str, *, remembered: bool
) -> None:
    keyboard.send_text(keys)
    require_trust([(Dependency.from_string("pip:numpy"), "pip:numpy")])
    assert trust_file.exists() is remembered
    if remembered:
        assert json.loads(trust_file.read_text())["packages"] == ["pip:numpy"]


@pytest.mark.parametrize("keys", ["\r", "\x1b[A\x1b[B\r", "x\r", "\x04", "\x1b"])
def test_enter_defaults_to_cancel_and_cancel_keys_never_save(keyboard: PipeInput, trust_file: Path, keys: str) -> None:
    keyboard.send_text(keys)
    with pytest.raises(TrustError, match="not approved"):
        require_trust([(Dependency.from_string("pip:numpy"), "pip:numpy")])
    assert not trust_file.exists()


def test_ctrl_c_interrupts_without_saving(keyboard: PipeInput, trust_file: Path) -> None:
    keyboard.send_text("\x03")
    with pytest.raises(KeyboardInterrupt):
        require_trust([(Dependency.from_string("pip:numpy"), "pip:numpy")])
    assert not trust_file.exists()


def test_closed_terminal_input_cancels_without_saving(keyboard: PipeInput, trust_file: Path) -> None:
    keyboard.close()
    with pytest.raises(TrustError, match="no input was available"):
        require_trust([(Dependency.from_string("pip:numpy"), "pip:numpy")])
    assert not trust_file.exists()


def test_redirected_stderr_keeps_warning_visible(
    keyboard: PipeInput,
    monkeypatch: pytest.MonkeyPatch,
    terminal: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    keyboard.send_text("\x1b[A\r")
    require_trust([(Dependency.from_string("pip:numpy"), "pip:numpy")])
    captured = terminal.readouterr()
    assert "WARNING" in captured.out
    assert "fully trust" in captured.out
    assert not captured.err

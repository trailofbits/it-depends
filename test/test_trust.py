import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import call, patch

import pytest

from it_depends.models import Dependency
from it_depends.repository import SourceRepository
from it_depends.trust import TrustError, require_trust

pytestmark = pytest.mark.usefixtures("terminal", "trust_file")

_PRIVATE_FILE_MODE = 0o600
_PRIVATE_DIRECTORY_MODE = 0o700


def _package(specification: str) -> tuple[Dependency, str]:
    return Dependency.from_string(specification), specification


def test_approval_is_remembered(trust_file: Path) -> None:
    with patch("builtins.input", return_value="permanent") as prompt:
        require_trust([_package("pip:numpy")])
        require_trust([_package("pip:numpy")])
    prompt.assert_called_once()
    assert json.loads(trust_file.read_text()) == {"directories": [], "packages": ["pip:numpy"]}


@pytest.mark.parametrize("kind", ["directory", "package"])
def test_run_only_approval_is_not_remembered(trust_file: Path, tmp_path: Path, kind: str) -> None:
    target = (SourceRepository(tmp_path), str(tmp_path)) if kind == "directory" else _package("pip:numpy")
    with patch("builtins.input", side_effect=["run", "cancel"]) as prompt:
        require_trust([target])
        with pytest.raises(TrustError):
            require_trust([target])
    assert prompt.call_args_list == [call(), call()]
    assert not trust_file.exists()


def test_run_only_approval_preserves_existing_store(trust_file: Path) -> None:
    with patch("builtins.input", return_value="permanent"):
        require_trust([_package("pip:existing")])
    original = trust_file.read_bytes()
    with patch("builtins.input", return_value="run"):
        require_trust([_package("pip:numpy")])
    assert trust_file.read_bytes() == original


@pytest.mark.parametrize("decisions", [("permanent", "run"), ("run", "permanent")])
def test_mixed_approvals_only_save_permanent_targets(trust_file: Path, decisions: tuple[str, str]) -> None:
    specifications = ["pip:numpy", "npm:lodash"]
    with patch("builtins.input", side_effect=decisions):
        require_trust([_package(specification) for specification in specifications])
    expected = [
        specification
        for specification, decision in zip(specifications, decisions, strict=True)
        if decision == "permanent"
    ]
    assert json.loads(trust_file.read_text())["packages"] == expected


def test_run_only_approval_covers_the_same_package_within_the_run(trust_file: Path) -> None:
    with patch("builtins.input", return_value="run") as prompt:
        require_trust([_package("pip:numpy"), _package("pip:numpy@=1.0.0")])
    prompt.assert_called_once()
    assert not trust_file.exists()


def test_run_only_approval_does_not_need_writable_configuration(trust_file: Path) -> None:
    with (
        patch("builtins.input", return_value="run"),
        patch.object(Path, "mkdir", side_effect=PermissionError("write denied")),
    ):
        require_trust([_package("pip:numpy")])
    assert not trust_file.exists()


@pytest.mark.parametrize("response", ["cancel", "", EOFError])
def test_denial_never_creates_store(trust_file: Path, response: str | type[Exception]) -> None:
    with (
        patch("builtins.input", side_effect=[response] if isinstance(response, str) else response),
        pytest.raises(TrustError),
    ):
        require_trust([_package("pip:numpy")])
    assert not trust_file.exists()


def test_interruption_never_creates_store(trust_file: Path) -> None:
    with patch("builtins.input", side_effect=KeyboardInterrupt), pytest.raises(KeyboardInterrupt):
        require_trust([_package("pip:numpy")])
    assert not trust_file.exists()


def test_invalid_response_reprompts(trust_file: Path) -> None:
    with patch("builtins.input", side_effect=["invalid", "permanent"]) as prompt:
        require_trust([_package("pip:numpy")])
    assert prompt.call_args_list == [call(), call()]
    assert trust_file.exists()


def test_all_targets_must_be_approved_before_saving(trust_file: Path) -> None:
    with patch("builtins.input", side_effect=["permanent", "cancel"]), pytest.raises(TrustError):
        require_trust([_package("pip:numpy"), _package("npm:lodash")])
    assert not trust_file.exists()


@pytest.mark.parametrize("stream_name", ["stdin", "stdout", "stderr"])
def test_headless_requires_explicit_flag(trust_file: Path, monkeypatch: pytest.MonkeyPatch, stream_name: str) -> None:
    monkeypatch.setattr(getattr(sys, stream_name), "isatty", lambda: False)
    with (
        patch("builtins.input", side_effect=AssertionError("must not prompt")),
        pytest.raises(TrustError, match="--trust"),
    ):
        require_trust([_package("pip:numpy")])
    assert not trust_file.exists()


def test_remembered_approval_works_headless(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("builtins.input", return_value="permanent"):
        require_trust([_package("pip:numpy")])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with patch("builtins.input", side_effect=AssertionError("must not prompt")):
        require_trust([_package("pip:numpy@=1.0.0")])


@pytest.mark.parametrize("existing", [None, "invalid JSON"])
def test_flag_bypasses_store_without_persisting(trust_file: Path, existing: str | None) -> None:
    if existing is not None:
        trust_file.parent.mkdir()
        trust_file.write_text(existing)
    with patch("builtins.input", side_effect=AssertionError("must not prompt")):
        require_trust([_package("pip:numpy"), _package("npm:lodash")], trust=True)
    if existing is None:
        assert not trust_file.exists()
    else:
        assert trust_file.read_text() == existing


def test_flag_does_not_require_access_to_store(trust_file: Path) -> None:
    with patch.object(Path, "read_text", side_effect=PermissionError):
        require_trust([_package("pip:numpy")], trust=True)
    assert not trust_file.exists()


@pytest.mark.parametrize(
    ("approved", "requested", "expected"),
    [
        ("pip:numpy", "pip:numpy@=1.0.0", True),
        ("pip:numpy", "pip:numpy@>=2.0.0", True),
        ("pip:numpy@=1.0.0", "pip:numpy@=1.0.0", True),
        ("pip:numpy@=1.0.0", "pip:numpy@=1.0.1", False),
        ("pip:numpy@1.0.0", "pip:numpy@=1.0.0", False),
        ("pip:numpy@>=1.0.0", "pip:numpy@>=1.0.0", True),
        ("pip:numpy@>=1.0.0", "pip:numpy@=2.0.0", False),
        ("pip:numpy@=1.0.0", "pip:numpy", False),
        ("pip:numpy@*", "pip:numpy", False),
        ("pip:numpy", "npm:numpy", False),
        ("pip:numpy", "pip:other", False),
        ("npm:@scope/pkg", "npm:@scope/pkg@1.0.0", True),
        ("npm:@scope/pkg", "npm:@other/pkg", False),
        ("npm:@scope/pkg@1.0.0", "npm:@scope/other@1.0.0", False),
        ("npm:@scope/pkg@1.0.0", "npm:@scope/pkg@2.0.0", False),
    ],
)
def test_package_approval_boundaries(trust_file: Path, approved: str, requested: str, *, expected: bool) -> None:
    with patch("builtins.input", return_value="permanent"):
        require_trust([_package(approved)])
    with patch("builtins.input", return_value="cancel") as prompt:
        if expected:
            require_trust([_package(requested)])
            prompt.assert_not_called()
        else:
            with pytest.raises(TrustError):
                require_trust([_package(requested)])
    assert json.loads(trust_file.read_text())["packages"] == [approved]


def test_directory_approval_is_exact_and_survives_content_changes(trust_file: Path, tmp_path: Path) -> None:
    directory = tmp_path / "project"
    child = directory / "child"
    child.mkdir(parents=True)
    with patch("builtins.input", return_value="permanent"):
        require_trust([(SourceRepository(directory), str(directory))])
    (directory / "changed.txt").touch()
    with patch("builtins.input", return_value="cancel") as prompt:
        require_trust([(SourceRepository(directory), str(directory))])
        prompt.assert_not_called()
        for other in (child, tmp_path):
            with pytest.raises(TrustError):
                require_trust([(SourceRepository(other), str(other))])
    assert json.loads(trust_file.read_text())["directories"] == [str(directory)]


def test_only_explicit_targets_are_saved(trust_file: Path) -> None:
    with patch("builtins.input", return_value="permanent"):
        require_trust([_package("pip:numpy"), _package("pip:numpy")])
    assert json.loads(trust_file.read_text())["packages"] == ["pip:numpy"]


@pytest.mark.parametrize(
    "contents",
    ["not JSON", "[]", '{"packages": true}', '{"packages": [true]}', '{"trust": true}', "\udcff"],
)
def test_malformed_store_fails_closed(trust_file: Path, contents: str) -> None:
    trust_file.parent.mkdir()
    trust_file.write_bytes(contents.encode("utf-8", errors="surrogateescape"))
    with pytest.raises(TrustError, match="Cannot read trust store"):
        require_trust([_package("pip:numpy")])


def test_unreadable_store_fails_closed(trust_file: Path) -> None:
    with (
        patch.object(Path, "read_text", side_effect=PermissionError("read denied")),
        pytest.raises(TrustError, match="read denied"),
    ):
        require_trust([_package("pip:numpy")])
    assert not trust_file.exists()


def test_unwritable_store_stops_analysis(trust_file: Path) -> None:
    trust_file.parent.write_text("not a directory")
    with patch("builtins.input", return_value="permanent"), pytest.raises(TrustError, match="trust store"):
        require_trust([_package("pip:numpy")])
    assert trust_file.parent.read_text() == "not a directory"


@pytest.mark.parametrize("operation", ["replace", "fsync"])
def test_failed_atomic_write_preserves_store(trust_file: Path, operation: str) -> None:
    with patch("builtins.input", return_value="permanent"):
        require_trust([_package("pip:numpy")])
    original = trust_file.read_bytes()
    boundary = Path if operation == "replace" else os
    with (
        patch("builtins.input", return_value="permanent"),
        patch.object(boundary, operation, side_effect=PermissionError("write denied")),
        pytest.raises(TrustError, match="Cannot save trust store"),
    ):
        require_trust([_package("npm:lodash")])
    assert trust_file.read_bytes() == original
    assert list(trust_file.parent.iterdir()) == [trust_file]


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")
def test_store_and_new_directory_are_private(trust_file: Path) -> None:
    with patch("builtins.input", return_value="permanent"):
        require_trust([_package("pip:numpy")])
    assert stat.S_IMODE(trust_file.stat().st_mode) == _PRIVATE_FILE_MODE
    assert stat.S_IMODE(trust_file.parent.stat().st_mode) == _PRIVATE_DIRECTORY_MODE


@pytest.mark.parametrize("columns", ["40", "80", "200"])
def test_target_rendering_cannot_interpret_markup_or_controls(
    tmp_path: Path,
    terminal: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    columns: str,
) -> None:
    monkeypatch.setenv("COLUMNS", columns)
    directory = tmp_path / "[red]hidden\n\x1b[2J"
    directory.mkdir()
    with patch("builtins.input", return_value="cancel"), pytest.raises(TrustError):
        require_trust([(SourceRepository(directory), str(directory))])
    captured = terminal.readouterr()
    assert not captured.out
    assert "[red]hidden\\n\\x1b[2J" in captured.err.replace("\n", "")
    assert "\x1b[2J" not in captured.err


def test_warning_explains_execution_and_persistence(terminal: pytest.CaptureFixture[str]) -> None:
    with patch("builtins.input", return_value="permanent"):
        require_trust([_package("pip:numpy")])
    captured = terminal.readouterr()
    assert not captured.out
    warning = " ".join(captured.err.split())
    for text in (
        "execute code",
        "dependencies",
        "user permissions",
        "does not sandbox",
        "future versions",
        "Trust permanently",
        "Trust for this run only",
        "Cancel (exit)",
    ):
        assert text in warning


@pytest.mark.parametrize("constraint", ["*", "not-a-version"])
def test_wildcard_resolution_scope_is_explicit(constraint: str, terminal: pytest.CaptureFixture[str]) -> None:
    with patch("builtins.input", return_value="cancel"), pytest.raises(TrustError):
        require_trust([_package(f"npm:example@{constraint}")])
    warning = " ".join(terminal.readouterr().err.split())
    assert "resolves all versions; approval covers this specification only" in warning

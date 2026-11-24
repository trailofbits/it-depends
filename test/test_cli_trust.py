import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from semantic_version import Version

from it_depends import _cli, trust
from it_depends.cache import PackageRepository
from it_depends.config import Settings
from it_depends.it_depends import APP_DIRS
from it_depends.models import Dependency, SourcePackage
from it_depends.repository import SourceRepository
from it_depends.resolution import resolve

pytestmark = pytest.mark.usefixtures("terminal", "trust_file")


@pytest.fixture
def resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Mock:
    packages = PackageRepository()
    packages.add(SourcePackage("demo", Version("1.0.0"), SourceRepository(tmp_path), "pip"))
    mock = Mock(return_value=packages)
    monkeypatch.setattr(_cli, "resolve", mock)
    monkeypatch.setattr(_cli, "DBPackageCache", MagicMock())
    return mock


def _arguments(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> None:
    monkeypatch.setattr(sys, "argv", ["it-depends", *arguments])


def test_partial_cyclonedx_without_a_root_does_not_report_a_saved_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    terminal: pytest.CaptureFixture[str],
    resolution: Mock,
) -> None:
    output = tmp_path / "report.json"
    output.write_text("existing report")
    resolution.side_effect = KeyboardInterrupt
    _arguments(
        monkeypatch, "pip:demo", "--trust", "--output-format", "cyclonedx", "--output-file", str(output), "--force"
    )
    with patch.object(_cli, "_approve_partial", return_value=True), pytest.raises(SystemExit) as raised:
        _cli.main()
    expected_status = 130
    assert raised.value.code == expected_status
    assert output.read_text() == "existing report"
    stderr = terminal.readouterr().err
    assert "No SBOM found" in stderr
    assert "Output saved" not in stderr
    assert "exported partial results" not in stderr


def test_denial_prevents_resolution_cache_deletion_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolution: Mock
) -> None:
    database = tmp_path / "dependencies.sqlite"
    database.write_text("keep this cache")
    output = tmp_path / "output.json"
    _arguments(
        monkeypatch,
        str(tmp_path),
        "--clear-cache",
        "--database",
        str(database),
        "--output-file",
        str(output),
    )
    with patch("it_depends.trust._choose_approval", return_value="cancel"), pytest.raises(SystemExit) as raised:
        _cli.main()
    assert raised.value.code == 1
    resolution.assert_not_called()
    assert database.read_text() == "keep this cache"
    assert not output.exists()


@pytest.mark.parametrize("first_decision", ["permanent", "run"])
def test_unapproved_comparison_prevents_both_analyses(
    trust_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    resolution: Mock,
    first_decision: str,
) -> None:
    _arguments(monkeypatch, "pip:numpy", "--compare", "npm:lodash")
    with (
        patch("it_depends.trust._choose_approval", side_effect=[first_decision, "cancel"]),
        pytest.raises(SystemExit) as raised,
    ):
        _cli.main()
    assert raised.value.code == 1
    resolution.assert_not_called()
    assert not trust_file.exists()


def test_interactive_run_only_approval_runs_without_saving(
    trust_file: Path, monkeypatch: pytest.MonkeyPatch, resolution: Mock
) -> None:
    _arguments(monkeypatch, "pip:numpy", "--compare", "pip:numpy")
    with patch("it_depends.trust._choose_approval", return_value="run") as prompt:
        _cli.main()
    prompt.assert_called_once()
    assert [call.args[0].package_full_name for call in resolution.call_args_list] == [
        "pip:numpy",
        "pip:numpy",
    ]
    assert not trust_file.exists()


@pytest.mark.parametrize("comparison", ["pip:numpy", "npm:lodash"])
def test_approved_comparison_runs_both_analyses(
    trust_file: Path, monkeypatch: pytest.MonkeyPatch, resolution: Mock, comparison: str
) -> None:
    _arguments(monkeypatch, "pip:numpy", "--compare", comparison)
    with patch("it_depends.trust._choose_approval", return_value="permanent") as prompt:
        _cli.main()
    assert [call.args[0].package_full_name for call in resolution.call_args_list] == [
        "pip:numpy",
        comparison,
    ]
    assert prompt.call_count == (1 if comparison == "pip:numpy" else 2)
    assert set(json.loads(trust_file.read_text())["packages"]) == {"pip:numpy", comparison}


def test_flag_approves_both_targets_without_saving(
    trust_file: Path, monkeypatch: pytest.MonkeyPatch, resolution: Mock
) -> None:
    _arguments(monkeypatch, "pip:numpy", "--compare", "npm:lodash", "--trust")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    with patch("it_depends.trust._choose_approval", side_effect=AssertionError("must not prompt")):
        _cli.main()
    assert [call.args[0].package_full_name for call in resolution.call_args_list] == [
        "pip:numpy",
        "npm:lodash",
    ]
    assert not trust_file.exists()


@pytest.mark.parametrize("location", ["target", "comparison"])
@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_directory_is_rejected_before_prompting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolution: Mock, location: str, kind: str
) -> None:
    invalid = tmp_path / "invalid"
    if kind == "file":
        invalid.touch()
    arguments = [str(invalid)] if location == "target" else ["pip:numpy", "--compare", str(invalid)]
    _arguments(monkeypatch, *arguments)
    with (
        patch("it_depends.trust._choose_approval", side_effect=AssertionError("must not prompt")),
        pytest.raises(SystemExit) as raised,
    ):
        _cli.main()
    assert raised.value.code == 1
    resolution.assert_not_called()


def test_canonical_paths_share_approval(
    tmp_path: Path, trust_file: Path, monkeypatch: pytest.MonkeyPatch, resolution: Mock
) -> None:
    directory = tmp_path / "project"
    directory.mkdir()
    link = tmp_path / "link"
    link.symlink_to(directory, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    _arguments(monkeypatch, "./project", "--compare", str(link))
    with patch("it_depends.trust._choose_approval", return_value="permanent") as prompt:
        _cli.main()
    prompt.assert_called_once()
    assert [call.args[0].path for call in resolution.call_args_list] == [
        directory.resolve(),
        directory.resolve(),
    ]
    assert json.loads(trust_file.read_text())["directories"] == [str(directory.resolve())]


def test_moving_directory_requires_new_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolution: Mock
) -> None:
    directory = tmp_path / "project"
    directory.mkdir()
    _arguments(monkeypatch, str(directory))
    with patch("it_depends.trust._choose_approval", return_value="permanent"):
        _cli.main()
    moved = directory.rename(tmp_path / "moved")
    resolution.reset_mock()
    _arguments(monkeypatch, str(moved))
    with patch("it_depends.trust._choose_approval", return_value="cancel"), pytest.raises(SystemExit):
        _cli.main()
    resolution.assert_not_called()


def test_clear_cache_preserves_trust(
    trust_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolution: Mock
) -> None:
    database = tmp_path / "dependencies.sqlite"
    database.touch()
    _arguments(monkeypatch, "pip:numpy", "--clear-cache", "--database", str(database))
    with patch("it_depends.trust._choose_approval", return_value="permanent"):
        _cli.main()
    assert not database.exists()
    assert json.loads(trust_file.read_text())["packages"] == ["pip:numpy"]
    resolution.assert_called_once()


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
def test_interrupted_prompt_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, resolution: Mock, error: type[Exception]
) -> None:
    _arguments(monkeypatch, "pip:numpy")
    with patch("it_depends.trust._choose_approval", side_effect=error), pytest.raises(SystemExit) as raised:
        _cli.main()
    assert raised.value.code == (130 if error is KeyboardInterrupt else 1)
    resolution.assert_not_called()


def test_help_shows_revocation_without_accessing_store(
    trust_file: Path, monkeypatch: pytest.MonkeyPatch, terminal: pytest.CaptureFixture[str]
) -> None:
    _arguments(monkeypatch, "--help")
    with (
        patch.object(Path, "read_text", side_effect=AssertionError("must not read store")),
        pytest.raises(SystemExit) as raised,
    ):
        _cli.main()
    assert raised.value.code == 0
    assert not trust_file.exists()
    help_text = "".join(terminal.readouterr().out.split())
    for text in (
        str(APP_DIRS.user_config_path / "trust.json"),
        "To revoke an approval, remove its entry",
        "delete the file to revoke all approvals",
        "--clear-cache does not revoke approvals",
    ):
        assert "".join(text.split()) in help_text


def test_listing_does_not_prompt_clear_cache_or_analyze(
    trust_file: Path, monkeypatch: pytest.MonkeyPatch, resolution: Mock
) -> None:
    database = trust_file.parent / "cache.sqlite"
    database.parent.mkdir()
    database.touch()
    _arguments(monkeypatch, "pip:numpy", "--list", "--clear-cache", "--database", str(database))
    with (
        patch.object(_cli, "_list_resolvers") as listing,
        patch("it_depends.trust._choose_approval", side_effect=AssertionError("must not prompt")),
    ):
        _cli.main()
    listing.assert_called_once()
    resolution.assert_not_called()
    assert database.exists()
    assert not trust_file.exists()


@pytest.mark.parametrize("source", ["environment", "dotenv", "secrets"])
def test_stored_settings_cannot_enable_trust(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    options = {}
    if source == "environment":
        monkeypatch.setenv("TRUST", "true")
    elif source == "dotenv":
        dotenv = tmp_path / ".env"
        dotenv.write_text("TRUST=true\n")
        options["_env_file"] = dotenv
    else:
        (tmp_path / "trust").write_text("true")
        options["_secrets_dir"] = tmp_path
    assert not Settings(_cli_parse_args=["pip:numpy"], **options).trust
    assert Settings(_cli_parse_args=["pip:numpy", "--trust"], **options).trust


def test_other_environment_settings_are_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUST", "true")
    workers = 3
    monkeypatch.setenv("MAX-WORKERS", str(workers))
    settings = Settings(_cli_parse_args=["pip:numpy"])
    assert not settings.trust
    assert settings.max_workers == workers


@pytest.mark.parametrize(
    ("specification", "package", "constraint"),
    [("npm:@scope/pkg", "@scope/pkg", "*"), ("npm:@scope/pkg@1.0.0", "@scope/pkg", "1.0.0")],
)
def test_scoped_package_identity_is_preserved(specification: str, package: str, constraint: str) -> None:
    dependency = _cli.parse_path_or_package_name(specification)
    assert isinstance(dependency, Dependency)
    assert dependency.package == package
    assert str(dependency.semantic_version) == constraint


def test_python_api_does_not_require_cli_approval(tmp_path: Path) -> None:
    with patch.object(trust, "require_trust", side_effect=AssertionError("CLI gate only")):
        assert not resolve(SourceRepository(tmp_path), depth_limit=0)


@pytest.mark.parametrize("level", ["debug", "info", "warning", "error", "critical"])
def test_stdout_is_raw_json_at_every_log_level(
    monkeypatch: pytest.MonkeyPatch,
    resolution: Mock,
    terminal: pytest.CaptureFixture[str],
    level: str,
) -> None:
    _arguments(monkeypatch, "pip:demo", "--trust", "--log-level", level)
    _cli.main()
    captured = terminal.readouterr()
    assert json.loads(captured.out) == resolution.return_value.to_obj()
    assert "WARNING" in captured.err


def test_failed_analysis_does_not_claim_an_existing_file_was_saved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resolution: Mock,
    terminal: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "report.json"
    output.write_text("original")
    resolution.side_effect = ValueError("resolver failed")
    _arguments(monkeypatch, "pip:demo", "--trust", "--force", "--output-file", str(output))
    with pytest.raises(SystemExit):
        _cli.main()
    assert output.read_text() == "original"
    assert "Output saved" not in terminal.readouterr().err

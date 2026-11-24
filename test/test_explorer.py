"""Keyboard navigation and offline explorer entry-point behavior."""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from textual.widgets import Input, Static

from it_depends import _cli
from it_depends.explorer.app import SavedExplorer
from it_depends.explorer.loaders import load_saved

_USAGE_ERROR = 2


@pytest.fixture
def saved_file(tmp_path: Path) -> Path:
    path = tmp_path / "report.json"
    packages = {}
    for name, dependency in (("root", "leaf"), ("leaf", "root")):
        packages[f"pip:{name}"] = {
            "1.0.0": {
                "name": name,
                "source": "pip",
                "version": "1.0.0",
                "dependencies": {f"pip:{dependency}": "*"},
                "vulnerabilities": ["CVE-example"] if name == "leaf" else [],
                "is_source_package": name == "root",
            }
        }
    path.write_text(json.dumps(packages))
    return path


@pytest.mark.parametrize("width", [40, 100])
def test_search_navigation_details_and_responsive_layout(saved_file: Path, width: int) -> None:
    graph = load_saved(saved_file)
    app = SavedExplorer(graph, str(saved_file))

    async def scenario() -> None:
        async with app.run_test(size=(width, 30)) as pilot:
            await pilot.press("/", "l", "e", "a", "f", "enter")
            tree = app.dependency_tree
            assert len(tree.root.children) == 1
            await pilot.press("right", "down")
            assert "CVE-example" in str(app.query_one("#details", Static).content)
            await pilot.press("right", "down", "down", "enter")
            await pilot.pause()
            selection = tree.cursor_node.data
            assert selection.kind == "component"
            assert graph.nodes[selection.key].name == "root"
            assert app.query_one("#search", Input).value == ""
            await pilot.press("tab")
            assert app.focused.id == "details-pane"
            details = app.query_one("#details-pane")
            narrow = 80
            if width < narrow:
                assert tree.region.bottom <= details.region.y
            else:
                assert tree.region.right <= details.region.x
            await pilot.press("escape")

    asyncio.run(scenario())


def test_empty_document_and_quit_do_not_wait_for_analysis(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text("{}")
    app = SavedExplorer(load_saved(path), str(path))

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.press("q")

    asyncio.run(scenario())
    assert app.return_code == 0


def test_explorer_cli_never_requests_trust_or_analyzes(
    saved_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["it-depends", "--explore", str(saved_file)])
    monkeypatch.setattr(_cli, "interactive_terminal", lambda: True)
    forbidden = Mock(side_effect=AssertionError("offline viewer cannot analyze or request trust"))
    monkeypatch.setattr(_cli, "require_trust", forbidden)
    monkeypatch.setattr(_cli, "DBPackageCache", forbidden)
    monkeypatch.setattr(_cli, "resolve", forbidden)
    with patch.object(SavedExplorer, "run") as run:
        _cli.main()
    run.assert_called_once()
    forbidden.assert_not_called()


def test_explorer_cli_rejects_headless_execution(saved_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["it-depends", "--explore", str(saved_file)])
    monkeypatch.setattr(_cli, "interactive_terminal", lambda: False)
    with pytest.raises(SystemExit) as raised:
        _cli.main()
    assert raised.value.code == _USAGE_ERROR


@pytest.mark.parametrize("flag", ["--trust", "--clear-cache", "--audit", "--list"])
def test_explorer_cli_rejects_analysis_options(
    saved_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    monkeypatch.setattr(sys, "argv", ["it-depends", "--explore", str(saved_file), flag])
    monkeypatch.setattr(_cli, "interactive_terminal", lambda: True)
    with pytest.raises(SystemExit) as raised:
        _cli.main()
    assert raised.value.code == _USAGE_ERROR


def test_missing_file_fails_before_starting_the_explorer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["it-depends", "--explore", str(tmp_path / "absent")])
    monkeypatch.setattr(_cli, "interactive_terminal", lambda: True)
    with patch.object(SavedExplorer, "run") as run, pytest.raises(SystemExit):
        _cli.main()
    run.assert_not_called()


def test_component_details_display_saved_vulnerability_applicability(tmp_path: Path) -> None:
    document = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "components": [{"type": "library", "name": "demo", "bom-ref": "demo", "version": "1.0"}],
        "vulnerabilities": [
            {
                "id": "CVE-example",
                "affects": [
                    {
                        "ref": "demo",
                        "versions": [
                            {"version": "1.0", "status": "unaffected"},
                            {"range": "vers:npm/>=2|<3", "status": "unknown"},
                            {"version": "4.0"},
                        ],
                    }
                ],
            },
        ],
    }
    path = tmp_path / "applicability.json"
    path.write_text(json.dumps(document))
    app = SavedExplorer(load_saved(path), str(path))

    async def scenario() -> None:
        async with app.run_test() as pilot:
            await pilot.press("/", "d", "enter", "right", "down")
            detail = str(app.query_one("#details", Static).content)
            assert "CVE-example\n  1.0 (unaffected)" in detail
            assert "vers:npm/>=2|<3 (unknown)" in detail
            assert "4.0 (affected)" in detail

    asyncio.run(scenario())

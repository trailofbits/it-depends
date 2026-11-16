import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from it_depends import trust


@pytest.fixture
def trust_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "config"
    monkeypatch.setattr(trust, "APP_DIRS", SimpleNamespace(user_config_path=directory))
    return directory / "trust.json"


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> pytest.CaptureFixture[str]:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        monkeypatch.setattr(type(stream), "isatty", lambda _stream: True)
    return capsys


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--runintegration",
        action="store_true",
        default=False,
        help="run integration tests",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: mark test as an integration test")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--runintegration"):
        # --runintegration given in cli: do not skip integration tests
        return
    skip_integration = pytest.mark.skip(reason="need --runintegration option to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)

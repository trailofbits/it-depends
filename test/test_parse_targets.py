from types import SimpleNamespace

import pytest

from it_depends._cli import _parse_targets


def test_parse_targets_missing_compare_path_exits(tmp_path, monkeypatch) -> None:
    """A missing --compare path exits like a missing target path.

    Before the fix, only the target path was validated, so a missing
    compare path sailed through to dependency resolution and failed
    there with a confusing error instead of a clean "does not exist".
    """
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        _parse_targets(SimpleNamespace(target=".", compare="does-not-exist"))


def test_parse_targets_existing_compare_path_passes(tmp_path, monkeypatch) -> None:
    """Existing target and compare paths pass validation."""
    (tmp_path / "other").mkdir()
    monkeypatch.chdir(tmp_path)
    _, to_compare = _parse_targets(SimpleNamespace(target=".", compare="other"))
    assert to_compare is not None

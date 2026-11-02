"""Tests for the executable resolution utility."""

from __future__ import annotations

from pathlib import Path

import pytest

from it_depends._exec import resolve_executable


def test_resolve_known_executable() -> None:
    """Resolving a known executable returns an absolute path."""
    path = resolve_executable("python3")
    assert Path(path).is_absolute()
    assert Path(path).is_file()


def test_resolve_nonexistent_raises() -> None:
    """Resolving a nonexistent executable raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="nonexistent_binary_xyz_123"):
        resolve_executable("nonexistent_binary_xyz_123")

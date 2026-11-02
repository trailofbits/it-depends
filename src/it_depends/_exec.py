"""Executable resolution utilities."""

from __future__ import annotations

import shutil


def resolve_executable(name: str) -> str:
    """Resolve a command name to its full filesystem path.

    Args:
        name: The name of the executable to locate.

    Returns:
        The fully-qualified path to the executable.

    Raises:
        FileNotFoundError: If the executable is not found on PATH.

    """
    path = shutil.which(name)
    if path is None:
        msg = f"`{name}` executable not found in PATH"
        raise FileNotFoundError(msg)
    return path

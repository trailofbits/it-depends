"""Require informed CLI approval before dynamically analyzing a target."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field
from rich.console import Console
from rich.prompt import Confirm
from rich.text import Text

from it_depends.it_depends import APP_DIRS
from it_depends.repository import SourceRepository

if TYPE_CHECKING:
    from collections.abc import Sequence

    from it_depends.models import Dependency

_WARNING = (
    "Analysis may execute code from this target and its dependencies with your user permissions. "
    "Internal Docker usage does not sandbox the entire analysis."
)


class TrustError(Exception):
    """An input lacks approval, or approval cannot be read or saved."""


class _TrustStore(BaseModel):
    """User-managed approvals, independent of environment and repository settings."""

    model_config = ConfigDict(extra="forbid", strict=True)

    directories: list[str] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)

    def approves(self, target: SourceRepository | Dependency, specification: str) -> bool:
        """Check the exact directory, package constraint, or unversioned package approval."""
        if isinstance(target, SourceRepository):
            return str(target.path) in self.directories
        return specification in self.packages or target.package_full_name in self.packages

    def remember(self, target: SourceRepository | Dependency, specification: str) -> None:
        """Record the requested target without granting approval to its dependencies."""
        if isinstance(target, SourceRepository):
            self.directories.append(str(target.path))
        else:
            self.packages.append(specification)


def _load_store(path: Path) -> _TrustStore:
    try:
        return _TrustStore.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _TrustStore()
    except (OSError, ValueError) as error:
        msg = (
            f"Cannot read trust store {str(path)!r}: {error}. "
            "Repair or delete this file, or use --trust to approve this run without persistence."
        )
        raise TrustError(msg) from error


def _atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".trust-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _save_store(path: Path, store: _TrustStore) -> None:
    try:
        _atomic_write(path, store.model_dump_json(indent=2) + "\n")
    except OSError as error:
        msg = (
            f"Cannot save trust store {str(path)!r}: {error}. "
            "Check directory permissions, or use --trust to approve this run without persistence."
        )
        raise TrustError(msg) from error


def _describe(target: SourceRepository | Dependency, specification: str) -> str:
    if isinstance(target, SourceRepository):
        return f"Directory {str(target.path)!r}"
    if specification == target.package_full_name:
        return f"Package {specification!r} (all versions, including future versions)"
    if str(target.semantic_version) == "*":
        return f"Package {specification!r} (resolves all versions; remembers this specification only)"
    return f"Package {specification!r} (the supplied version constraint)"


def _confirm(console: Console, target: SourceRepository | Dependency, specification: str) -> None:
    console.print(Text(_describe(target, specification)))
    console.print(Text(_WARNING))
    if not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)):
        msg = (
            "Target is not trusted. Interactive approval requires a terminal. "
            "Rerun with --trust to approve the targets and their dependencies for this run only."
        )
        raise TrustError(msg)
    try:
        approved = Confirm.ask("Trust this target and remember the approval?", default=False, console=console)
    except EOFError as error:
        msg = "Trust approval cancelled: no input was available. No analysis was started."
        raise TrustError(msg) from error
    if not approved:
        msg = "Target was not approved. No analysis was started."
        raise TrustError(msg)


def require_trust(targets: Sequence[tuple[SourceRepository | Dependency, str]], *, trust: bool = False) -> None:
    """Approve all CLI targets before resolution, optionally remembering interactive consent.

    Args:
        targets: Parsed targets paired with their original package specifications. Directory
            paths must already be resolved and validated by the caller.
        trust: Explicit current-run authorization; bypasses the store without changing it.

    Raises:
        TrustError: An approval is missing, declined, or cannot be read or persisted.
        KeyboardInterrupt: The user interrupts interactive approval.

    """
    console = Console(stderr=True, highlight=False)
    if trust:
        console.print(Text(_WARNING))
        return
    path = APP_DIRS.user_config_path / "trust.json"
    store = _load_store(path)
    approved = False
    for target, specification in targets:
        if store.approves(target, specification):
            continue
        _confirm(console, target, specification)
        store.remember(target, specification)
        approved = True
    if approved:
        _save_store(path, store)

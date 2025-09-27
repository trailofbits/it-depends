"""Source repository management module for handling git repositories and filesystem paths."""

from __future__ import annotations

import atexit
import subprocess
from pathlib import Path
from shutil import rmtree, which
from tempfile import mkdtemp


class SourceRepository:
    """Represents a repo that we are analyzing from source."""

    def __init__(self, path: Path | str) -> None:
        """Initialize the source repository.

        Args:
            path: Path to the repository (can be string or Path object)

        """
        super().__init__()
        if not isinstance(path, Path):
            path = Path(path)
        self.path: Path = path

    @staticmethod
    def from_git(git_url: str) -> SourceRepository:
        """Create a source repository from a git URL."""
        tmpdir = mkdtemp()

        def cleanup() -> None:
            """Clean up temporary directory."""
            rmtree(tmpdir, ignore_errors=True)

        atexit.register(cleanup)

        # Find git executable safely
        git_path = which("git")
        if git_path is None:
            error_msg = "git executable not found in PATH"
            raise RuntimeError(error_msg)

        subprocess.check_call([git_path, "clone", git_url], cwd=tmpdir)  # noqa: S603
        for file in Path(tmpdir).iterdir():
            if file.is_dir():
                return SourceRepository(file)
        msg = f"Error cloning {git_url}"
        raise ValueError(msg)

    @staticmethod
    def from_filesystem(path: str | Path) -> SourceRepository:
        """Create a source repository from a filesystem path."""
        return SourceRepository(path)

    def __repr__(self) -> str:
        """Get the representation of the repository."""
        return f"{self.__class__.__name__}({str(self.path)!r})"

    def __str__(self) -> str:
        """Get the string representation of the repository."""
        return str(self.path)

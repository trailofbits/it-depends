import atexit
from pathlib import Path
from shutil import rmtree
from subprocess import check_call
from tempfile import mkdtemp
from typing import Union


class SourceRepository:
    """represents a repo that we are analyzing from source"""

    def __init__(self, path: Union[Path, str]):
        super().__init__()
        if not isinstance(path, Path):
            path = Path(path)
        self.path: Path = path

    @staticmethod
    def from_git(git_url: str) -> "SourceRepository":
        tmpdir = mkdtemp()

        def cleanup():
            rmtree(tmpdir, ignore_errors=True)

        atexit.register(cleanup)

        check_call(["git", "clone", git_url], cwd=tmpdir)
        for file in Path(tmpdir).iterdir():
            if file.is_dir():
                return SourceRepository(file)
        raise ValueError(f"Error cloning {git_url}")

    @staticmethod
    def from_filesystem(path: Union[str, Path]) -> "SourceRepository":
        return SourceRepository(path)

    def __repr__(self):
        return f"{self.__class__.__name__}({str(self.path)!r})"

    def __str__(self):
        return str(self.path)

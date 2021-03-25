from pathlib import Path
import re
from subprocess import check_call
from tempfile import TemporaryDirectory
from typing import Iterable, Iterator, List, Optional, Tuple
from urllib import request

from .dependencies import Dependency, DependencyClassifier, DependencyResolver, SourceRepository, Package, PackageCache


GITHUB_URL_MATCH = re.compile(r"\s*https?://(www\.)?github.com/([^/]+)/(.+)\s*", re.IGNORECASE)
REQUIRE_LINE_REGEX = r"\s*([^\s]+)\s+([^\s]+)\s*(//\s*indirect\s*)?"
REQUIRE_LINE_MATCH = re.compile(REQUIRE_LINE_REGEX)
REQUIRE_MATCH = re.compile(fr"\s*require\s+{REQUIRE_LINE_REGEX}")
REQUIRE_BLOCK_MATCH = re.compile(r"\s*require\s+\(\s*")
MODULE_MATCH = re.compile(r"\s*module\s+([^\s]+)\s*")


class GoModule:
    def __init__(self, name: str, dependencies: Iterable[Tuple[str, str]]):
        self.name: str = name
        self.dependencies: List[Tuple[str, str]] = list(dependencies)

    @staticmethod
    def tag_to_git_hash(tag: str) -> str:
        segments = tag.split("-")
        if len(segments) == 3:
            return segments[-1]
        else:
            return tag

    @staticmethod
    def parse_mod(mod_content: str) -> "GoModule":
        in_require = False
        dependencies = []
        name = None
        for line in mod_content.split("\n"):
            if not in_require:
                m = REQUIRE_MATCH.match(line)
                if m:
                    dependencies.append((m.group(1), m.group(2)))
                else:
                    if name is None:
                        m = MODULE_MATCH.match(line)
                        if m:
                            name = m.group(1)
                            continue
                    in_require = bool(REQUIRE_BLOCK_MATCH.match(line))
            elif line.strip() == ")":
                in_require = False
            else:
                m = REQUIRE_LINE_MATCH.match(line)
                if m:
                    dependencies.append((m.group(1), m.group(2)))
        if name is None:
            raise ValueError("Missing `module` line in go mod specification")
        return GoModule(name, dependencies)

    @staticmethod
    def from_github(github_org: str, github_repo: str, tag: str):
        github_url = f"https://raw.githubusercontent.com/{github_org}/{github_repo}/{tag}/go.mod"
        with request.urlopen(github_url) as response:
            return GoModule.parse_mod(response.read())

    @staticmethod
    def from_git(git_url: str, tag: str):
        m = GITHUB_URL_MATCH.match(git_url)
        if m:
            return GoModule.from_github(m.group(2), m.group(3), tag)
        with TemporaryDirectory() as tempdir:
            check_call(["git", "clone", git_url], cwd=tempdir)
            for content in Path(tempdir).iterdir():
                if content.is_dir():
                    git_dir = content
                    break
            else:
                raise ValueError(f"Error cloning {git_url}")
            check_call(["git", "checkout", GoModule.tag_to_git_hash(tag)], cwd=git_dir)
            with open(git_dir / "go.mod", "r") as f:
                return GoModule.parse_mod(f.read())

    @staticmethod
    def from_name(module_name: str, tag: str):
        return GoModule.from_git(f"https://{module_name}", tag)

    @staticmethod
    def load(name_or_url: str, tag: str = "master"):
        if not name_or_url.startswith("http://") and not name_or_url.startswith("https://"):
            return GoModule.from_name(name_or_url, tag)
        else:
            return GoModule.from_git(name_or_url, tag)


class GoResolver(DependencyResolver):
    def resolve_from_git(self, git_url: str, tag: Optional[str] = None):
        pass

    def resolve_missing(self, dependency: Dependency) -> Iterator[Package]:
        pass


class GoClassifier(DependencyClassifier):
    name = "npm"
    description = "classifies the dependencies of JavaScript packages using `npm`"

    def can_classify(self, repo: SourceRepository) -> bool:
        return (repo.path / "go.mod").exists()

    def classify(self, repo: SourceRepository, cache: Optional[PackageCache] = None):
        resolver = GoResolver(source=self, cache=cache)
        repo.resolvers.append(resolver)
        repo.add()
        resolver.resolve_unsatisfied(repo)

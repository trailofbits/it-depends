from datetime import datetime
from logging import getLogger
import os
from pathlib import Path
import re
from subprocess import check_call, check_output, DEVNULL, CalledProcessError
from tempfile import TemporaryDirectory
from typing import Iterable, Iterator, List, Optional, Tuple, Union
from urllib import request
from urllib.error import HTTPError

from .dependencies import (
    Dependency, DependencyClassifier, DependencyResolver, SourcePackage, SourceRepository, Package, PackageCache,
    SemanticVersion
)

from semantic_version import Version
from semantic_version.base import BaseSpec, Range, SimpleSpec


log = getLogger(__file__)

GITHUB_URL_MATCH = re.compile(r"\s*https?://(www\.)?github.com/([^/]+)/(.+?)(\.git)?\s*", re.IGNORECASE)
REQUIRE_LINE_REGEX = r"\s*([^\s]+)\s+([^\s]+)\s*(//\s*indirect\s*)?"
REQUIRE_LINE_MATCH = re.compile(REQUIRE_LINE_REGEX)
REQUIRE_MATCH = re.compile(fr"\s*require\s+{REQUIRE_LINE_REGEX}")
REQUIRE_BLOCK_MATCH = re.compile(r"\s*require\s+\(\s*")
MODULE_MATCH = re.compile(r"\s*module\s+([^\s]+)\s*")

GOPATH: Optional[str] = os.environ.get("GOPATH", None)


def git_commit(path: Optional[str] = None) -> Optional[str]:
    try:
        return check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            stderr=DEVNULL
        )
    except CalledProcessError:
        return None


class GoVersion:
    def __init__(self, go_version_string: str):
        self.version_string: str = go_version_string
        self.build: bool = False  # This is to appease semantic_version.base.SimpleSpec

    def __eq__(self, other):
        return isinstance(other, GoVersion) and self.version_string == other.version_string

    def __hash__(self):
        return hash(self.version_string)

    def __str__(self):
        return self.version_string


@BaseSpec.register_syntax
class GoSpec(SimpleSpec):
    SYNTAX = 'go'

    class Parser(SimpleSpec.Parser):
        @classmethod
        def parse(cls, expression):
            return Range(operator=Range.OP_EQ, target=GoVersion(expression))


class GoModule:
    def __init__(self, name: str, dependencies: Iterable[Tuple[str, str]] = ()):
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
    def parse_mod(mod_content: Union[str, bytes]) -> "GoModule":
        if isinstance(mod_content, bytes):
            mod_content = mod_content.decode("utf-8")
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
        try:
            with request.urlopen(github_url) as response:
                return GoModule.parse_mod(response.read())
        except HTTPError as e:
            if e.code == 404:
                # If there is no `go.mod`, it likely means the package has no dependencies:
                return GoModule(f"github.com/{github_org}/{github_repo}")
            raise

    @staticmethod
    def from_git(git_url: str, tag: str):
        m = GITHUB_URL_MATCH.fullmatch(git_url)
        if m:
            return GoModule.from_github(m.group(2), m.group(3), tag)
        module_name = git_url
        if module_name.startswith("http://"):
            module_name = module_name[len("http://"):]
        elif module_name.startswith("https://"):
            module_name = module_name[len("https://"):]
        if not git_url.endswith(".git"):
            git_url = f"{git_url}.git"
        else:
            module_name = module_name[:-len(".git")]
        log.info(f"Attempting to clone {git_url}")
        with TemporaryDirectory() as tempdir:
            check_call(["git", "init"], cwd=tempdir, stderr=DEVNULL, stdout=DEVNULL)
            check_call(["git", "remote", "add", "origin", git_url], cwd=tempdir, stderr=DEVNULL, stdout=DEVNULL)
            git_hash = GoModule.tag_to_git_hash(tag)
            env = {
                "GIT_TERMINAL_PROMPT": "0"
            }
            if os.environ.get("GIT_SSH", "") == "" and os.environ.get("GIT_SSH_COMMAND", "") == "":
                # disable any ssh connection pooling by git
                env["GIT_SSH_COMMAND"] = "ssh -o ControlMaster=no"
            check_call(["git", "fetch", "--depth", "1", "origin", git_hash], cwd=tempdir, stderr=DEVNULL,
                       stdout=DEVNULL, env=env)
            go_mod_path = Path(tempdir) / "go.mod"
            if not go_mod_path.exists():
                # the package likely doesn't have any dependencies
                return GoModule(module_name)
            with open(Path(tempdir) / "go.mod", "r") as f:
                return GoModule.parse_mod(f.read())

    @staticmethod
    def from_name(module_name: str, tag: str):
        if GOPATH is not None:
            # see if we have the source in our
            pass
        return GoModule.from_git(f"https://{module_name}", tag)

    @staticmethod
    def load(name_or_url: str, tag: str = "master"):
        if not name_or_url.startswith("http://") and not name_or_url.startswith("https://"):
            return GoModule.from_name(name_or_url, tag)
        else:
            return GoModule.from_git(name_or_url, tag)


class GoResolver(DependencyResolver):
    def __init__(self, cache: Optional[PackageCache] = None):
        super().__init__(source=GoClassifier.default_instance(), cache=cache)

    def resolve_missing(self, dependency: Dependency) -> Iterator[Package]:
        assert isinstance(dependency.semantic_version, GoSpec)
        version_string = str(dependency.semantic_version)
        module = GoModule.from_name(dependency.package, version_string)
        yield Package(
            name=module.name,
            version=GoVersion(version_string),  # type: ignore
            source=self.source,
            dependencies=[
                Dependency(package=package, semantic_version=GoSpec(version))
                for package, version in module.dependencies
            ]
        )


class GoClassifier(DependencyClassifier):
    name = "go"
    description = "classifies the dependencies of JavaScript packages using `npm`"

    @classmethod
    def parse_spec(cls, spec: str) -> SemanticVersion:
        return GoSpec(spec)

    @classmethod
    def parse_version(cls, version_string: str) -> Version:
        return GoVersion(version_string)  # type: ignore

    def can_classify(self, repo: SourceRepository) -> bool:
        return (repo.path / "go.mod").exists()

    def classify(self, repo: SourceRepository, cache: Optional[PackageCache] = None):
        resolver = GoResolver(cache=cache)
        repo.resolvers.append(resolver)
        with open(repo.path / "go.mod") as f:
            module = GoModule.parse_mod(f.read())
        git_hash = git_commit(str(repo.path))
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        version = f"v0.0.0-{timestamp}-"
        if git_hash is None:
            version = f"{version}????"
        else:
            version = f"{version}{git_hash}"
        repo.add(SourcePackage(
            name=module.name,
            version=GoVersion(version),  # type: ignore
            source_path=repo.path,
            source=self,
            dependencies=[
                Dependency(package=package, semantic_version=GoSpec(version))
                for package, version in module.dependencies
            ]
        ))
        resolver.resolve_unsatisfied(repo)

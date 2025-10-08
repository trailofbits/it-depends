"""Functions to automatically download source repositories from various VCS systems and providers.

Logic largely taken from the implementation of `go get`:

    https://golang.org/src/cmd/go/internal/vcs/vcs.go
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from re import Pattern
from typing import TYPE_CHECKING, TypeVar, cast

from typing_extensions import Self

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


class VCSResolutionError(ValueError):
    """Raised when VCS resolution fails."""


class GoVCSConfigError(VCSResolutionError):
    """Raised when Go VCS configuration is invalid."""


T = TypeVar("T")


class VCS:
    """Base class for version control systems."""

    _DEFAULT_INSTANCE: VCS

    def __init__(self, name: str, cmd: str, scheme: Iterable[str], ping_cmd: Iterable[str]) -> None:
        """Initialize a VCS instance.

        Args:
            name: Name of the VCS system
            cmd: Command to execute
            scheme: List of URL schemes supported
            ping_cmd: Command to test repository accessibility

        """
        self.name: str = name
        self.cmd: str = cmd
        self.scheme: list[str] = list(scheme)
        self.ping_cmd: list[str] = list(ping_cmd)

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Set the default instance when subclassing."""
        cls._DEFAULT_INSTANCE = cls()  # type: ignore[call-arg]

    @classmethod
    def default_instance(cls) -> Self:
        """Get the default instance of this VCS class."""
        return cast("T", cls._DEFAULT_INSTANCE)  # type: ignore[valid-type]

    def ping(self, repo: str) -> str | None:
        """Test if a repository is accessible.

        Args:
            repo: Repository URL to test

        Returns:
            The first working scheme, or None if none work

        """
        env = {"GIT_TERMINAL_PROMPT": "0"}
        if os.environ.get("GIT_SSH", "") == "" and os.environ.get("GIT_SSH_COMMAND", "") == "":
            # disable any ssh connection pooling by git
            env["GIT_SSH_COMMAND"] = "ssh -o ControlMaster=no"
        for scheme in self.scheme:
            cmd = [self.cmd] + [c.replace("{scheme}", scheme).replace("{repo}", repo) for c in self.ping_cmd]
            # Note: repo is validated by the caller, this is safe
            if subprocess.call(cmd, stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=env) == 0:  # noqa: S603
                return scheme
        return None

    def __hash__(self) -> int:
        """Hash based on VCS name."""
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        """Check equality with another VCS instance."""
        return isinstance(other, VCS) and self.name == other.name


class Git(VCS):
    """Git version control system."""

    def __init__(self) -> None:
        """Initialize Git VCS with standard configuration."""
        super().__init__(
            name="Git",
            cmd="git",
            scheme=("git", "https", "http", "git+ssh", "ssh"),
            ping_cmd=("ls-remote", "{scheme}://{repo}"),
        )


VCSes: list[VCS] = [vcs.default_instance() for vcs in (Git,)]

# VCS_MOD is a stub for the "mod" scheme. It's returned by
# repoRootForImportPathDynamic, but is otherwise not treated as a VCS command.
VCS_MOD = VCS(name="mod", cmd="", scheme=(), ping_cmd=())


@dataclass
class Match:
    """Represents a VCS path match."""

    prefix: str
    import_path: str
    repo: str = ""
    vcs: str = ""
    root: str | None = None

    def expand(self, s: str) -> str:
        """Expand placeholders in a string using match attributes.

        Args:
            s: String containing placeholders

        Returns:
            String with placeholders replaced

        """
        for key, value in self.__dict__.items():
            if not key.startswith("_"):
                s = s.replace(f"{{{key}}}", value)
        return s


REGEXP_TYPE = Pattern[str]


@dataclass
class VCSPath:
    """Configuration for a VCS path pattern."""

    regexp: REGEXP_TYPE
    repo: str = ""
    path_prefix: str = ""
    check: Callable[[Match], None] | None = None
    vcs: str | None = None
    schemeless_repo: bool = False


class VCSMatchError(VCSResolutionError):
    """Raised when VCS path matching fails."""


def no_vcs_suffix(match: Match) -> None:
    """Check that the repository name does not end in .foo for any version control system foo.

    The usual culprit is ".git".

    Args:
        match: The match object to check

    Raises:
        VCSMatchError: If repository has invalid VCS suffix

    """
    repo = match.repo
    for vcs in VCSes:
        if repo.endswith(f".{vcs.cmd}"):
            msg = f"Invalid version control suffix in {match.prefix!r} path"
            raise VCSMatchError(msg)


VCS_PATHS: list[VCSPath] = []


def _register(path: VCSPath) -> VCSPath:
    """Register a VCS path pattern."""
    VCS_PATHS.append(path)
    return path


GITHUB = _register(
    VCSPath(
        path_prefix="github.com",
        regexp=re.compile(r"^(?P<root>github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)(/[A-Za-z0-9_.\-]+)*$"),
        vcs="git",
        repo="https://{root}",
        check=no_vcs_suffix,
    )
)


GENERAL_REPO = _register(
    VCSPath(
        regexp=re.compile(
            r"(?P<root>(?P<repo>([a-z0-9\-]+\.)+[a-z0-9.\-]+(:[0-9]+)?(/~?[A-Za-z0-9_.\-]+)+?)\."
            r"(?P<vcs>bzr|fossil|git|hg|svn))(/~?[A-Za-z0-9_.\-]+)*$"
        ),
        schemeless_repo=True,
    )
)


@dataclass
class Repository:
    """Represents a resolved repository."""

    repo: str
    root: str
    vcs: VCS
    is_custom: bool = False


def vcs_by_cmd(cmd: str) -> VCS | None:
    """Get the version control system for the given command name (hg, git, svn, bzr).

    Args:
        cmd: Command name to look up

    Returns:
        VCS instance or None if not found

    """
    for vcs in VCSes:
        if cmd == vcs.cmd:
            return vcs
    return None


@dataclass
class GoVCSRule:
    """Represents a Go VCS rule."""

    pattern: str
    allowed: list[str]


DEFAULT_GO_VCS: list[GoVCSRule] = [
    GoVCSRule("private", ["all"]),
    GoVCSRule("public", ["git", "hg"]),
]


GO_VCS_RULES: list[GoVCSRule] | None = None


def parse_go_vcs(s: str) -> list[GoVCSRule] | None:
    """Parse Go VCS configuration string.

    Args:
        s: Configuration string to parse

    Returns:
        List of VCS rules or None if empty

    Raises:
        GoVCSConfigError: If configuration is invalid

    """
    s = s.strip()
    if not s:
        return None
    rules: list[GoVCSRule] = []
    have: dict[str, str] = {}
    for item_raw in s.split(","):
        item = item_raw.strip()
        if not item:
            msg = "Empty entry in GOVCS"
            raise GoVCSConfigError(msg)
        i = item.find(":")
        if i < 0:
            msg = f"Malformed entry in GOVCS (missing colon): {item!r}"
            raise GoVCSConfigError(msg)
        pattern, vcs_list = item[:i].strip(), item[i + 1 :].strip()
        if not pattern:
            msg = f"Empty pattern in GOVCS: {item!r}"
            raise GoVCSConfigError(msg)
        if not vcs_list:
            msg = f"Empty VCS list in GOVCS: {item!r}"
            raise GoVCSConfigError(msg)
        if not Path(pattern).is_absolute():
            msg = f"Relative pattern not allowed in GOVCS: {pattern!r}"
            raise GoVCSConfigError(msg)
        if have.get(pattern, ""):
            msg = f"Unreachable pattern in GOVCS: {item!r} after {have[pattern]!r}"
            raise GoVCSConfigError(msg)
        have[pattern] = item
        allowed = [a.strip() for a in vcs_list.split("|")]
        if any(not a for a in allowed):
            msg = f"Empty VCS name in GOVCS: {item!r}"
            raise GoVCSConfigError(msg)
        rules.append(GoVCSRule(pattern=pattern, allowed=allowed))
    return rules


def check_go_vcs(vcs: VCS, _root: str) -> None:
    """Check if VCS is allowed for the given root.

    Args:
        vcs: Version control system to check
        _root: Repository root path (unused but kept for future implementation)

    """
    if vcs == VCS_MOD:
        return
    global GO_VCS_RULES  # noqa: PLW0603
    if GO_VCS_RULES is None:
        GO_VCS_RULES = parse_go_vcs(os.getenv("GOVCS", ""))
        if GO_VCS_RULES is None:
            GO_VCS_RULES = []
        GO_VCS_RULES.extend(DEFAULT_GO_VCS)
    # TODO(@team): Eventually consider implementing this GOVCS check   # noqa: FIX002, TD003
    """
    private := module.MatchPrefixPatterns(cfg.GOPRIVATE, root)
    if !govcs.allow(root, private, vcs.Cmd) {
     what := "public"
     if private {
         what = "private"
     }
     return fmt.Errorf("GOVCS disallows using %s for %s %s; see 'go help vcs'", vcs.Cmd, what, root)
    }
    """


def resolve(path: str) -> Repository:  # noqa: C901, PLR0912
    """Resolve a VCS path to a repository.

    Args:
        path: Import path to resolve

    Returns:
        Repository object

    Raises:
        VCSMatchError: If path cannot be matched
        VCSResolutionError: If VCS resolution fails

    """
    for service in VCS_PATHS:
        if not path.startswith(service.path_prefix):
            continue
        m = service.regexp.match(path)
        if m is None and service.path_prefix:
            msg = f"Invalid {service.path_prefix} import path {path!r}"
            raise VCSMatchError(msg)
        match = Match(prefix=f"{service.path_prefix}/", import_path=path)
        if m:
            for name, value in m.groupdict().items():
                if name and value:
                    setattr(match, name, value)
        if service.vcs is not None:
            match.vcs = match.expand(service.vcs)
        if service.repo:
            match.repo = match.expand(service.repo)
        if service.check is not None:
            service.check(match)
        vcs = vcs_by_cmd(match.vcs)
        if vcs is None:
            msg = f"unknown version control system {match.vcs!r}"
            raise VCSResolutionError(msg)
        if match.root is None:
            msg = f"{match!r} was expected to have a non-None root!"
            raise VCSResolutionError(msg)
        check_go_vcs(vcs, match.root)
        if not service.schemeless_repo:
            repo_url: str = match.repo
        else:
            scheme = vcs.ping(match.repo)
            if scheme is None:
                scheme = vcs.scheme[0]
            repo_url = f"{scheme}://{match.repo}"
        return Repository(repo=repo_url, root=match.root, vcs=vcs)
    msg = f"Unable to resolve repository for {path!r}"
    raise VCSResolutionError(msg)

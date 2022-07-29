"""
Functions to automatically download source repositories from various VCS systems and providers.
Logic largely taken from the implementation of `go get`:

    https://golang.org/src/cmd/go/internal/vcs/vcs.go

"""
import sys
from dataclasses import dataclass
import os
import re
from re import Pattern
import subprocess
from typing import Callable, cast, Dict, Iterable, List, Optional, Type, TypeVar


class VCSResolutionError(ValueError):
    pass


class GoVCSConfigError(VCSResolutionError):
    pass


T = TypeVar("T")


class VCS:
    _DEFAULT_INSTANCE: "VCS"

    def __init__(self, name: str, cmd: str, scheme: Iterable[str], ping_cmd: Iterable[str]):
        self.name: str = name
        self.cmd: str = cmd
        self.scheme: List[str] = list(scheme)
        self.ping_cmd: List[str] = list(ping_cmd)

    def __init_subclass__(cls, **kwargs):
        setattr(cls, "_DEFAULT_INSTANCE", cls())

    @classmethod
    def default_instance(cls: Type[T]) -> T:
        return cast(T, getattr(cls, "_DEFAULT_INSTANCE"))

    def ping(self, repo: str) -> Optional[str]:
        env = {"GIT_TERMINAL_PROMPT": "0"}
        if os.environ.get("GIT_SSH", "") == "" and os.environ.get("GIT_SSH_COMMAND", "") == "":
            # disable any ssh connection pooling by git
            env["GIT_SSH_COMMAND"] = "ssh -o ControlMaster=no"
        for scheme in self.scheme:
            cmd = [self.cmd] + [
                c.replace("{scheme}", scheme).replace("{repo}", repo) for c in self.ping_cmd
            ]
            if (
                subprocess.call(cmd, stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=env)
                == 0
            ):
                return scheme
        return None

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return isinstance(other, VCS) and self.name == other.name


class Git(VCS):
    def __init__(self):
        super().__init__(
            name="Git",
            cmd="git",
            scheme=("git", "https", "http", "git+ssh", "ssh"),
            ping_cmd=("ls-remote", "{scheme}://{repo}"),
        )


VCSes: List[VCS] = [vcs.default_instance() for vcs in (Git,)]

# VCS_MOD is a stub for the "mod" scheme. It's returned by
# repoRootForImportPathDynamic, but is otherwise not treated as a VCS command.
VCS_MOD = VCS(name="mod", cmd="", scheme=(), ping_cmd=())


@dataclass
class Match:
    prefix: str
    import_path: str
    repo: str = ""
    vcs: str = ""
    root: Optional[str] = None

    def expand(self, s: str) -> str:
        for key, value in self.__dict__.items():
            if not key.startswith("_"):
                s = s.replace(f"{{{key}}}", value)
        return s


if sys.version_info >= (3, 9):
    REGEXP_TYPE = Pattern[str]
else:
    REGEXP_TYPE = Pattern


@dataclass
class VCSPath:
    regexp: REGEXP_TYPE
    repo: str = ""
    path_prefix: str = ""
    check: Optional[Callable[[Match], None]] = None
    vcs: Optional[str] = None
    schemeless_repo: bool = False


class VCSMatchError(VCSResolutionError):
    pass


def no_vcs_suffix(match: Match):
    """
    checks that the repository name does not end in .foo for any version control system foo.
    The usual culprit is ".git".

    """
    repo = match.repo
    for vcs in VCSes:
        if repo.endswith(f".{vcs.cmd}"):
            raise VCSMatchError(f"Invalid version control suffix in {match.prefix!r} path")


VCS_PATHS: List[VCSPath] = []


def _register(path: VCSPath) -> VCSPath:
    VCS_PATHS.append(path)
    return path


GITHUB = _register(
    VCSPath(
        path_prefix="github.com",
        regexp=re.compile(
            r"^(?P<root>github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)(/[A-Za-z0-9_.\-]+)*$"
        ),
        vcs="git",
        repo="https://{root}",
        check=no_vcs_suffix,
    )
)


GENERAL_REPO = _register(
    VCSPath(
        regexp=re.compile(
            r"(?P<root>(?P<repo>([a-z0-9.\-]+\.)+[a-z0-9.\-]+(:[0-9]+)?(/~?[A-Za-z0-9_.\-]+)+?)\."
            r"(?P<vcs>bzr|fossil|git|hg|svn))(/~?[A-Za-z0-9_.\-]+)*$"
        ),
        schemeless_repo=True,
    )
)


@dataclass
class Repository:
    repo: str
    root: str
    vcs: VCS
    is_custom: bool = False


def vcs_by_cmd(cmd: str) -> Optional[VCS]:
    """vcsByCmd returns the version control system for the given command name (hg, git, svn, bzr)."""
    for vcs in VCSes:
        if cmd == vcs.cmd:
            return vcs
    return None


@dataclass
class GoVCSRule:
    pattern: str
    allowed: List[str]


DEFAULT_GO_VCS: List[GoVCSRule] = [
    GoVCSRule("private", ["all"]),
    GoVCSRule("public", ["git", "hg"]),
]


GO_VCS_RULES: Optional[List[GoVCSRule]] = None


def parse_go_vcs(s: str) -> Optional[List[GoVCSRule]]:
    s = s.strip()
    if not s:
        return None
    rules: List[GoVCSRule] = []
    have: Dict[str, str] = {}
    for item in s.split(","):
        item = item.strip()
        if not item:
            raise GoVCSConfigError(f"Empty entry in GOVCS")
        i = item.find(":")
        if i < 0:
            raise GoVCSConfigError(f"Malformed entry in GOVCS (missing colon): {item!r}")
        pattern, vcs_list = item[:i].strip(), item[i + 1 :].strip()
        if not pattern:
            raise GoVCSConfigError(f"Empty pattern in GOVCS: {item!r}")
        if not vcs_list:
            raise GoVCSConfigError(f"Empty VCS list in GOVCS: {item!r}")
        if not os.path.isabs(pattern):
            raise GoVCSConfigError(f"Relative pattern not allowed in GOVCS: {pattern!r}")
        if have.get(pattern, default=""):
            raise GoVCSConfigError(
                f"Unreachable pattern in GOVCS: {item!r} after {have[pattern]!r}"
            )
        have[pattern] = item
        allowed = [a.strip() for a in vcs_list.split("|")]
        if any(not a for a in allowed):
            raise GoVCSConfigError(f"Empty VCS name in GOVCS: {item!r}")
        rules.append(GoVCSRule(pattern=pattern, allowed=allowed))
    return rules


def check_go_vcs(vcs: VCS, root: str):
    if vcs == VCS_MOD:
        return
    global GO_VCS_RULES
    if GO_VCS_RULES is None:
        GO_VCS_RULES = parse_go_vcs(os.getenv("GOVCS", ""))
        if GO_VCS_RULES is None:
            GO_VCS_RULES = []
        GO_VCS_RULES.extend(DEFAULT_GO_VCS)
    # TODO: Eventually consider implementing this GOVCS check:
    # private := module.MatchPrefixPatterns(cfg.GOPRIVATE, root)
    # if !govcs.allow(root, private, vcs.Cmd) {
    # 	what := "public"
    # 	if private {
    # 		what = "private"
    # 	}
    # 	return fmt.Errorf("GOVCS disallows using %s for %s %s; see 'go help vcs'", vcs.Cmd, what, root)
    # }


def resolve(path: str) -> Repository:
    for service in VCS_PATHS:
        if not path.startswith(service.path_prefix):
            continue
        m = service.regexp.match(path)
        if m is None:
            if service.path_prefix:
                raise VCSMatchError(f"Invalid {service.path_prefix} import path {path!r}")
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
            raise VCSResolutionError(f"unknown version control system {match.vcs!r}")
        elif match.root is None:
            raise VCSResolutionError(f"{match!r} was expected to have a non-None root!")
        check_go_vcs(vcs, match.root)
        if not service.schemeless_repo:
            repo_url: str = match.repo
        else:
            scheme = vcs.ping(match.repo)
            if scheme is None:
                scheme = vcs.scheme[0]
            repo_url = f"{scheme}://{match.repo}"
        return Repository(repo=repo_url, root=match.root, vcs=vcs)
    raise VCSResolutionError(f"Unable to resolve repository for {path!r}")

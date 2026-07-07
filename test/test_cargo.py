import json

import pytest

from it_depends.cache import InMemoryPackageCache
from it_depends.cargo import CargoResolver, _parse_workspace_member, get_dependencies
from it_depends.models import Dependency, Package, SourcePackage
from it_depends.repository import SourceRepository


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        ("serde 1.0.0 (path+file:///home/user/serde)", "serde"),
        ("my-crate 0.1.0 (/path/to/my-crate)", "my-crate"),
        ("path+file:///home/user/serde#serde@1.0.0", "serde"),
        ("path+file:///home/user/serde#serde", "serde"),
        ("path+file:///path/to/crate#my-crate@0.1.0", "my-crate"),
    ],
)
def test_parse_workspace_member(member: str, expected: str) -> None:
    assert _parse_workspace_member(member) == expected


def test_parse_workspace_member_unknown_format() -> None:
    result = _parse_workspace_member("something-unexpected")
    assert result == "something-unexpected"


def _dep(name: str) -> Dependency:
    return Dependency(package=name, source="cargo", semantic_version=CargoResolver.parse_spec("*"))


def test_get_dependencies_ignores_optional_deps(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    """Optional (feature-gated) deps are not tracked; only explicitly required deps survive.

    Regression test for #189/#191: a crate may declare a mutually exclusive set of optional
    deps, so forcing them all into the graph can describe a configuration with no valid
    resolution. `get_dependencies` keeps only the deps cargo requires under the active feature
    set, dropping optional and dev/build deps.
    """
    metadata = {
        "packages": [
            {
                "name": "myapp",
                "version": "0.1.0",
                "dependencies": [
                    {"name": "serde", "req": "^1", "kind": None, "optional": False},
                    {"name": "rayon", "req": "^1", "kind": None, "optional": True},
                    {"name": "criterion", "req": "^0.5", "kind": "dev", "optional": False},
                ],
            },
        ],
        "workspace_members": ["myapp 0.1.0 (path+file:///tmp/myapp)"],
    }
    monkeypatch.setattr(
        "it_depends.cargo.subprocess.check_output",
        lambda *args, **kwargs: json.dumps(metadata).encode(),  # noqa: ARG005
    )

    packages = list(get_dependencies(SourceRepository(path=tmp_path), cargo_path="cargo"))

    assert len(packages) == 1
    assert {dep.package for dep in packages[0].dependencies} == {"serde"}


def test_resolve_from_source_only_marks_resolved_deps(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    """A required dep with no matching package must not be marked resolved.

    Regression test for #189: the old shortcut marked every declared dependency as resolved,
    including deps that `cargo metadata` never adds to the resolved package set (e.g. a subcrate
    whose `cargo metadata` errored). Those deps then matched nothing and their subtrees were
    pruned. Such deps must be left unresolved so normal resolution can retry them.
    """
    repo = SourceRepository(path=tmp_path)

    # `libfoo` is actually resolved (a package is yielded); `libbar` and `libqux` are required
    # deps that cargo failed to resolve, so no package is yielded for them.
    source_pkg = SourcePackage(
        name="myapp",
        version="0.1.0",
        source_repo=repo,
        source="cargo",
        dependencies=[_dep("libfoo"), _dep("libbar")],
    )
    libfoo = Package(
        name="libfoo",
        version="1.0.0",
        source="cargo",
        dependencies=[_dep("libqux")],
    )

    monkeypatch.setattr(CargoResolver, "can_resolve_from_source", lambda self, repo: True)  # noqa: ARG005
    monkeypatch.setattr("it_depends.cargo.get_dependencies", lambda repo, cargo_path=None: iter([source_pkg, libfoo]))  # noqa: ARG005

    resolver = CargoResolver()
    resolver._tool_path = "cargo"  # noqa: SLF001  # avoid resolve_executable in this unit test

    cache = InMemoryPackageCache()
    with cache:
        result = resolver.resolve_from_source(repo, cache)

    assert result is source_pkg
    # libfoo has a matching package in the cache -> resolved.
    assert cache.was_resolved(_dep("libfoo"))
    # libbar and libqux resolved to nothing -> left unresolved so normal resolution can retry them.
    assert not cache.was_resolved(_dep("libbar"))
    assert not cache.was_resolved(_dep("libqux"))

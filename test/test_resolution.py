"""Tests for the dependency resolution pipeline.

These cover the decision paths of `resolve()` (depth limiting, input dispatch,
dependency de-duplication, cache short-circuits, resolver update failures and
concurrency) and of `resolve_sbom()` (which resolutions are accepted, rejected,
or skipped as duplicates).
"""

import gc
import itertools
import logging
from collections.abc import Iterator
from pathlib import Path
from unittest import TestCase, mock

import pytest
from semantic_version import SimpleSpec

from it_depends.cache import InMemoryPackageCache, PackageRepository
from it_depends.dependencies import (
    Dependency,
    DependencyResolver,
    Package,
    PackageCache,
    ResolverAvailability,
    SourcePackage,
    SourceRepository,
    Version,
    resolver_by_name,
    resolvers,
)
from it_depends.resolution import resolve, resolve_sbom

_FAKE_IDS = itertools.count()


class TestResolution(TestCase):
    """Decision paths of `resolve()`."""

    def setUp(self) -> None:
        class FakeResolver(DependencyResolver):
            name: str = "fake"
            description: str = "Used by the resolution tests"

            def is_available(self) -> ResolverAvailability:
                return ResolverAvailability(is_available=True)

            def can_resolve_from_source(self, repo: SourceRepository) -> bool:  # noqa: ARG002
                return self.source_resolvable

            def resolve_from_source(
                self,
                repo: SourceRepository,  # noqa: ARG002
                cache: PackageCache | None = None,  # noqa: ARG002
            ) -> SourcePackage | None:
                return self.source_package

            def can_update_dependencies(self, package: Package) -> bool:
                return package.name in self.updatable

            def update_dependencies(self, package: Package) -> Package:
                self.update_calls.append(package.name)
                if package.name in self.update_errors:
                    raise self.update_errors[package.name]
                return package.update_dependencies(self.updated_deps.get(package.name, frozenset()))

            def resolve(self, dependency: Dependency) -> Iterator[Package]:
                self.resolve_calls.append(dependency.package)
                yield from self.resolved.get(dependency.package, [])

        # A unique name keeps `resolver_by_name()`'s cache from resolving to a
        # fake left over from another test.
        FakeResolver.name = f"fake{next(_FAKE_IDS)}"
        resolvers.cache_clear()
        resolver_by_name.cache_clear()
        self.fake_cls = FakeResolver
        self.resolver = FakeResolver()
        # State lives on the instance rather than in `__init__`: `resolvers()`
        # re-instantiates every subclass, which would otherwise reset it.
        self.resolver.resolved = {}
        self.resolver.resolve_calls = []
        self.resolver.update_calls = []
        self.resolver.updatable = set()
        self.resolver.update_errors = {}
        self.resolver.updated_deps = {}
        self.resolver.source_resolvable = False
        self.resolver.source_package = None

        # Keep the pipeline hermetic: only the fake resolver participates.
        patcher = mock.patch("it_depends.resolution.resolvers", return_value=frozenset({self.resolver}))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        # Stop the patches registered in setUp before dropping the fake, and
        # release every reference to it: a fake left registered as a resolver
        # subclass would be picked up by later tests.
        self.doCleanups()
        del self.resolver
        del self.fake_cls
        resolvers.cache_clear()
        resolver_by_name.cache_clear()
        gc.collect()
        gc.collect()

    def _dep(self, name: str, spec: str = "*") -> Dependency:
        return Dependency(package=name, source=self.resolver, semantic_version=SimpleSpec(spec))

    def _pkg(self, name: str, version: str = "1.0.0", dependencies: tuple[Dependency, ...] = ()) -> Package:
        return Package(
            name=name,
            version=Version.coerce(version),
            source=self.resolver,
            dependencies=dependencies,
        )

    def test_depth_limit_zero_returns_empty_repository(self) -> None:
        """`depth_limit=0` short-circuits before any resolver is consulted."""
        root = self._pkg("root", dependencies=(self._dep("child"),))
        self.resolver.resolved["child"] = [self._pkg("child")]

        repo = resolve(root, depth_limit=0, max_workers=1)

        assert isinstance(repo, PackageRepository)
        assert len(repo) == 0
        assert self.resolver.resolve_calls == []

    def test_unsupported_input_raises_type_error(self) -> None:
        """A value that is neither Package, Dependency nor SourceRepository is rejected."""
        with pytest.raises(TypeError, match="repo_or_spec must be either a Package, Dependency, or SourceRepository"):
            resolve("not-a-package")  # type: ignore[arg-type]

    def test_resolves_dependencies_transitively(self) -> None:
        """A dependency is resolved, and the packages it yields are updated in turn."""
        child = self._pkg("child", dependencies=(self._dep("grandchild"),))
        self.resolver.resolved["root-dep"] = [child]
        self.resolver.resolved["grandchild"] = [self._pkg("grandchild")]

        repo = resolve(self._dep("root-dep"), max_workers=1)

        assert {p.name for p in repo} == {"child", "grandchild"}
        assert sorted(self.resolver.resolve_calls) == ["grandchild", "root-dep"]

    def test_depth_limit_bounds_how_far_transitive_resolution_goes(self) -> None:
        """Each extra unit of `depth_limit` follows exactly one more hop of dependencies."""
        self.resolver.resolved["root-dep"] = [self._pkg("first", dependencies=(self._dep("second-dep"),))]
        self.resolver.resolved["second-dep"] = [self._pkg("second", dependencies=(self._dep("third-dep"),))]
        self.resolver.resolved["third-dep"] = [self._pkg("third")]

        shallow = resolve(self._dep("root-dep"), depth_limit=1, max_workers=1)
        deep = resolve(self._dep("root-dep"), depth_limit=2, max_workers=1)

        assert {p.name for p in shallow} == {"first", "second"}
        assert {p.name for p in deep} == {"first", "second", "third"}

    def test_dependency_required_by_two_packages_is_resolved_once(self) -> None:
        """A dependency that two packages share is queued and resolved only once."""
        shared = self._dep("shared")
        first = self._pkg("first", dependencies=(shared,))
        second = self._pkg("second", dependencies=(shared,))
        self.resolver.resolved["root-dep"] = [first, second]
        self.resolver.resolved["shared"] = [self._pkg("shared")]

        repo = resolve(self._dep("root-dep"), max_workers=1)

        assert {p.name for p in repo} == {"first", "second", "shared"}
        assert self.resolver.resolve_calls.count("shared") == 1

    def test_package_input_caches_dependencies_but_not_the_root(self) -> None:
        """The supplied package is not written to the cache; the packages it pulls in are."""
        cache = InMemoryPackageCache()
        root = self._pkg("root", dependencies=(self._dep("child"),))
        self.resolver.resolved["child"] = [self._pkg("child")]

        resolve(root, cache=cache, max_workers=1)

        assert ("root" in {p.name for p in cache}) is False
        assert {p.name for p in cache} == {"child"}

    def test_cached_transitive_dependency_is_not_resolved_again(self) -> None:
        """A dependency already resolved in the cache is taken from the cache, not the resolver."""
        cache = InMemoryPackageCache()
        cached_child = self._pkg("child", "1.0.0")
        cache.add(cached_child)
        cache.set_resolved(self._dep("child"))
        # A different version would be returned if the resolver were consulted.
        self.resolver.resolved["child"] = [self._pkg("child", "2.0.0")]
        self.resolver.resolved["root-dep"] = [self._pkg("root-pkg", dependencies=(self._dep("child"),))]

        repo = resolve(self._dep("root-dep"), cache=cache, max_workers=1)

        assert self.resolver.resolve_calls == ["root-dep"]
        child = next(p for p in repo if p.name == "child")
        assert child.version == Version.coerce("1.0.0")

    def test_resolver_update_adds_new_dependencies_and_records_the_update(self) -> None:
        """An updating resolver's new dependency is queued, and the update is recorded."""
        root = self._pkg("root", dependencies=(self._dep("child"),))
        child = self._pkg("child")
        new_dep = self._dep("from-update")
        self.resolver.updatable = {"child"}
        self.resolver.updated_deps["child"] = frozenset({new_dep})
        self.resolver.resolved["child"] = [child]
        self.resolver.resolved["from-update"] = [self._pkg("from-update")]
        cache = InMemoryPackageCache()

        repo = resolve(root, cache=cache, max_workers=1)

        assert {p.name for p in repo} == {"root", "child", "from-update"}
        updated = next(p for p in repo if p.name == "child")
        assert updated.dependencies == frozenset({new_dep})
        assert repo.updated_by(updated) == {self.resolver.name}
        assert cache.was_updated(updated, self.resolver.name) is True

    def test_resolver_update_failure_is_logged_and_does_not_abort(self) -> None:
        """A resolver raising while updating is logged, and the package keeps its dependencies."""
        root = self._pkg("root")
        self.resolver.updatable = {"root"}
        self.resolver.update_errors["root"] = ValueError("boom")

        with self.assertLogs("it_depends.resolution", level=logging.WARNING) as logs:
            repo = resolve(root, max_workers=1)

        assert {p.name for p in repo} == {"root"}
        assert not next(p for p in repo if p.name == "root").dependencies
        assert "boom" in "\n".join(logs.output)

    def test_unexpected_resolver_update_error_is_logged_and_does_not_abort(self) -> None:
        """A non-ValueError raised while updating is logged and resolution continues."""
        root = self._pkg("root", dependencies=(self._dep("child"),))
        self.resolver.resolved["child"] = [self._pkg("child")]
        self.resolver.updatable = {"child"}
        self.resolver.update_errors["child"] = RuntimeError("kaboom")

        with self.assertLogs("it_depends.resolution", level=logging.ERROR) as logs:
            repo = resolve(root, max_workers=1)

        assert {p.name for p in repo} == {"root", "child"}
        assert "kaboom" in "\n".join(logs.output)

    def test_root_already_recorded_as_updated_in_the_cache_is_not_updated_again(self) -> None:
        """A root package the cache already records as updated is added without re-updating it."""
        cache = InMemoryPackageCache()
        # The cache must hold something for the `was_updated` check to apply at all.
        cache.add(self._pkg("unrelated"))
        root = self._pkg("root")
        cache.set_updated(root, self.resolver.name)
        self.resolver.updatable = {"root"}
        self.resolver.updated_deps["root"] = frozenset({self._dep("should-not-appear")})

        repo = resolve(root, cache=cache, max_workers=1)

        assert {p.name for p in repo} == {"root"}
        assert not next(p for p in repo if p.name == "root").dependencies

    def test_package_already_updated_in_the_cache_is_not_updated_again(self) -> None:
        """A package the cache records as updated is reused instead of updated a second time."""
        cache = InMemoryPackageCache()
        child = self._pkg("child")
        cache.add(child)
        cache.set_resolved(self._dep("child"))
        cache.set_updated(child, self.resolver.name)
        self.resolver.updatable = {"child"}
        self.resolver.resolved["root-dep"] = [self._pkg("root-pkg", dependencies=(self._dep("child"),))]

        repo = resolve(self._dep("root-dep"), cache=cache, max_workers=1)

        assert {p.name for p in repo} == {"root-pkg", "child"}
        assert repo.updated_by(child) == frozenset()
        # The updater returning an unchanged package by default hides a stray
        # call, so assert directly that it was never asked to update anything.
        assert self.resolver.update_calls == []

    def test_cache_recording_an_update_without_the_package_keeps_the_resolved_package(self) -> None:
        """When the cache records an update but stores no package, the resolved package is kept."""
        cache = InMemoryPackageCache()
        # The cache must be nonempty for the `was_updated` check to apply, and it
        # must not hold `child`: resolving a Dependency would add it to the cache
        # before the update check, so `child` is passed as the root instead.
        cache.add(self._pkg("unrelated"))
        child = self._pkg("child")
        cache.set_updated(child, self.resolver.name)
        self.resolver.updatable = {"child"}

        repo = resolve(child, cache=cache, max_workers=1)

        assert set(repo) == {child}

    def test_concurrent_package_update_is_applied(self) -> None:
        """With several workers a package-update result is still recorded on the repository."""
        root = self._pkg("root")
        new_dep = self._dep("from-update")
        self.resolver.updatable = {"root"}
        self.resolver.updated_deps["root"] = frozenset({new_dep})
        self.resolver.resolved["from-update"] = [self._pkg("from-update")]

        repo = resolve(root, max_workers=2)

        assert {p.name for p in repo} == {"root", "from-update"}
        assert next(p for p in repo if p.name == "root").dependencies == frozenset({new_dep})

    def test_source_repository_resolves_to_source_package(self) -> None:
        """A source repository resolvable by a resolver contributes its source package."""
        source_repo = SourceRepository(Path())
        source_package = SourcePackage(
            name="self",
            version=Version.coerce("0.1.0"),
            source_repo=source_repo,
            source=self.resolver,
        )
        self.resolver.source_resolvable = True
        self.resolver.source_package = source_package
        cache = InMemoryPackageCache()

        repo = resolve(source_repo, cache=cache, max_workers=1)

        assert {p.name for p in repo} == {"self"}
        assert isinstance(next(iter(repo)), SourcePackage)
        assert len(cache) == 0

    def test_source_resolver_that_returns_no_package_is_skipped(self) -> None:
        """A resolver that claims a source repository but yields nothing is skipped, not trusted."""
        self.resolver.source_resolvable = True
        self.resolver.source_package = None

        with pytest.raises(ValueError, match="Can not resolve"):
            resolve(SourceRepository(Path()), max_workers=1)

    def test_unresolvable_source_repository_raises_with_unavailable_resolvers(self) -> None:
        """When no resolver claims a source repository, the error names the unavailable ones."""
        self.resolver.source_resolvable = False
        patcher = mock.patch.object(
            type(self.resolver),
            "is_available",
            return_value=ResolverAvailability(is_available=False, reason="no toolchain"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        with pytest.raises(ValueError, match="Can not resolve") as excinfo:
            resolve(SourceRepository(Path()), max_workers=1)
        assert "no toolchain" in str(excinfo.value)

    def test_concurrent_resolution_matches_sequential(self) -> None:
        """The threaded path resolves the same package set as the sequential one."""
        child = self._pkg("child", dependencies=(self._dep("grandchild"),))
        self.resolver.resolved["root-dep"] = [child]
        self.resolver.resolved["grandchild"] = [self._pkg("grandchild")]

        sequential = {p.name for p in resolve(self._dep("root-dep"), max_workers=1)}
        self.resolver.resolve_calls.clear()
        concurrent = {p.name for p in resolve(self._dep("root-dep"), max_workers=2)}

        assert sequential == concurrent == {"child", "grandchild"}


class TestResolveSBOM(TestCase):
    """Decision paths of `resolve_sbom()`."""

    def setUp(self) -> None:
        class FakeResolver(DependencyResolver):
            name: str = "fake-sbom"
            description: str = "Used by the SBOM resolution tests"

            def is_available(self) -> ResolverAvailability:
                return ResolverAvailability(is_available=True)

            def can_resolve_from_source(self, repo: SourceRepository) -> bool:  # noqa: ARG002
                return False

            def resolve_from_source(
                self,
                repo: SourceRepository,  # noqa: ARG002
                cache: PackageCache | None = None,  # noqa: ARG002
            ) -> SourcePackage | None:
                return None

            def resolve(self, dependency: Dependency) -> Iterator[Package]:  # noqa: ARG002
                return iter(())

        FakeResolver.name = f"sbomfake{next(_FAKE_IDS)}"
        resolvers.cache_clear()
        resolver_by_name.cache_clear()
        self.fake_cls = FakeResolver
        self.resolver = FakeResolver()

    def tearDown(self) -> None:
        # Stop the patches registered in setUp before dropping the fake, and
        # release every reference to it: a fake left registered as a resolver
        # subclass would be picked up by later tests.
        self.doCleanups()
        del self.resolver
        del self.fake_cls
        resolvers.cache_clear()
        resolver_by_name.cache_clear()
        gc.collect()
        gc.collect()

    def _dep(self, name: str, spec: str = "*") -> Dependency:
        return Dependency(package=name, source=self.resolver, semantic_version=SimpleSpec(spec))

    def _pkg(self, name: str, version: str = "1.0.0", dependencies: tuple[Dependency, ...] = ()) -> Package:
        return Package(
            name=name,
            version=Version.coerce(version),
            source=self.resolver,
            dependencies=dependencies,
        )

    def test_package_without_dependencies_yields_a_single_root_sbom(self) -> None:
        """A leaf package is its own complete resolution."""
        root = self._pkg("root")
        cache = InMemoryPackageCache()

        sboms = list(resolve_sbom(root, cache))

        assert len(sboms) == 1
        assert sboms[0].root_packages == frozenset({root})
        assert sboms[0].packages == frozenset({root})

    def test_matching_packages_produce_one_sbom_each(self) -> None:
        """Every version satisfying the constraint yields its own resolution."""
        old = self._pkg("app", "1.0.0")
        new = self._pkg("app", "2.0.0")
        cache = InMemoryPackageCache()
        cache.add(old)
        cache.add(new)
        root = self._pkg("root", dependencies=(self._dep("app", ">=1.0.0"),))

        sboms = list(resolve_sbom(root, cache, order_ascending=False))

        versions = {next(p for p in sbom.packages if p.name == "app").version for sbom in sboms}
        assert versions == {old.version, new.version}
        assert len(sboms) == 2  # noqa: PLR2004

    def test_conflicting_version_constraints_yield_no_sbom(self) -> None:
        """Two packages requiring incompatible versions of one dependency is not a resolution."""
        first = self._pkg("first", dependencies=(self._dep("shared", "==1.0.0"),))
        second = self._pkg("second", dependencies=(self._dep("shared", "==2.0.0"),))
        cache = InMemoryPackageCache()
        cache.add(first)
        cache.add(second)
        cache.add(self._pkg("shared", "1.0.0"))
        cache.add(self._pkg("shared", "2.0.0"))
        root = self._pkg("root", dependencies=(self._dep("first"), self._dep("second")))

        assert list(resolve_sbom(root, cache)) == []

    def test_unsatisfied_dependency_yields_no_sbom(self) -> None:
        """A dependency with no matching package cannot be completed."""
        cache = InMemoryPackageCache()
        root = self._pkg("root", dependencies=(self._dep("missing"),))

        assert list(resolve_sbom(root, cache)) == []

    def test_sbom_records_the_dependency_edges(self) -> None:
        """The emitted SBOM records the (dependent, dependency) edge."""
        app = self._pkg("app")
        cache = InMemoryPackageCache()
        cache.add(app)
        root = self._pkg("root", dependencies=(self._dep("app"),))

        (sbom,) = list(resolve_sbom(root, cache))

        assert sbom.dependencies == frozenset({(root, app)})

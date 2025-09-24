"""Dependency resolution module for managing package dependencies."""

from __future__ import annotations

import logging
import sys
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from multiprocessing import cpu_count
from typing import TYPE_CHECKING

from tqdm import tqdm

from .cache import InMemoryPackageCache, PackageCache, PackageRepository
from .models import Dependency, Package, SourcePackage
from .repository import SourceRepository
from .resolver import PartialResolution, resolvers
from .sbom import SBOM

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

logger = logging.getLogger(__name__)


class _DependencyResult:
    """Result of dependency resolution."""

    def __init__(self, dep: Dependency, packages: list[Package], depth: int) -> None:
        """Initialize dependency result."""
        self.dep: Dependency = dep
        self.packages: list[Package] = packages
        self.depth: int = depth


def _process_dep(dep: Dependency, depth: int) -> _DependencyResult:
    return _DependencyResult(dep=dep, packages=list(dep.resolver.resolve(dep)), depth=depth)


class _PackageResult:
    """Result of package update."""

    def __init__(
        self,
        package: Package,
        *,
        was_updated: bool,
        updated_in_resolvers: set[str],
        depth: int,
    ) -> None:
        """Initialize package result."""
        self.package: Package = package
        self.was_updated: bool = was_updated
        self.updated_in_resolvers: set[str] = set(updated_in_resolvers)
        self.depth: int = depth


def _update_package(package: Package, depth: int) -> _PackageResult:
    """Update a package's dependencies."""
    old_deps = frozenset(package.dependencies)
    uir: list[str] = []
    for resolver in resolvers():
        if resolver.can_update_dependencies(package):
            try:
                updated_package = resolver.update_dependencies(package)
                if updated_package.dependencies != old_deps:
                    uir.append(resolver.name)
                    package = updated_package
            except ValueError as e:
                logger.warning("Error updating dependencies: %s", str(e))
            except Exception:
                logger.exception("Error updating dependencies")
    return _PackageResult(
        package=package,
        was_updated=len(uir) > 0,
        updated_in_resolvers=set(uir),
        depth=depth,
    )


def _resolve_dependency(dep: Dependency, depth: int) -> _DependencyResult:
    """Resolve a dependency to packages."""
    for resolver in resolvers():
        if resolver.name == dep.source:
            try:
                packages = list(resolver.resolve(dep))
                return _DependencyResult(dep=dep, packages=packages, depth=depth)
            except Exception:
                logger.exception("Error updating dependencies")
    return _DependencyResult(dep=dep, packages=[], depth=depth)


def resolve_sbom(root_package: Package, packages: PackageCache, order_ascending: bool = True) -> Iterator[SBOM]:  # noqa: FBT001, FBT002
    """Generate SBOMs from packages.

    Args:
        root_package: The root package to generate SBOMs for
        packages: The package repository containing all packages
        order_ascending: If True, prefer older versions; if False, prefer newer versions

    Yields:
        SBOM objects representing different dependency resolutions

    """
    if not root_package.dependencies:
        yield SBOM(root_packages=(root_package,))
        return

    logger.info("Resolving the %s possible SBOM for %s", ["newest", "oldest"][order_ascending], root_package.name)

    stack: list[PartialResolution] = [PartialResolution(packages=(root_package,))]

    history: set[PartialResolution] = {pr for pr in stack if pr.is_valid}

    while stack:
        pr = stack.pop()
        if pr.is_complete:
            yield SBOM(pr.dependencies(), root_packages=(root_package,))
            continue
        elif not pr.is_valid:
            continue

        for dep, required_by in pr.packages.unsatisfied_dependencies():  # type: ignore[attr-defined]
            if not PartialResolution(packages=required_by, parent=pr).is_valid:
                continue
            for match in sorted(packages.match(dep), key=lambda p: p.version, reverse=order_ascending):
                next_pr = pr.add(required_by, match)
                if next_pr.is_valid and next_pr not in history:
                    history.add(next_pr)
                    stack.append(next_pr)


def resolve(  # noqa: C901, PLR0912, PLR0915
    repo_or_spec: Package | Dependency | SourceRepository,
    *,
    cache: PackageCache | None = None,
    depth_limit: int = -1,
    repo: PackageRepository | None = None,
    max_workers: int | None = None,
) -> PackageRepository:
    """Resolve the dependencies for a package, dependency, or source repository.

    If depth_limit is negative (the default), recursively resolve all dependencies.
    If depth_limit is greater than zero, only recursively resolve dependencies to that depth.
    max_workers controls the number of spawned threads, if None cpu_count is used.
    """
    if depth_limit == 0:
        return PackageRepository()

    if repo is None:
        repo = PackageRepository()

    if cache is None:
        cache = InMemoryPackageCache()  # Some resolvers may use it to save temporary results

    if max_workers is None:
        max_workers = cpu_count()

    try:
        with tqdm(desc=f"resolving {repo_or_spec!s}", leave=False, unit=" dependencies") as t:
            # Initialize variables
            unresolved_dependencies: list[tuple[Dependency, int]] = []
            unupdated_packages: list[tuple[Package, int]] = []

            if isinstance(repo_or_spec, SourceRepository):
                # Resolve from source repository
                found_source_package = False
                for resolver in resolvers():
                    if resolver.can_resolve_from_source(repo_or_spec):
                        source_package = resolver.resolve_from_source(repo_or_spec, cache=cache)
                        if source_package is None:
                            continue
                        found_source_package = True
                        unupdated_packages.append((source_package, 0))
                if not found_source_package:
                    error_msg = f"Can not resolve {repo_or_spec}"
                    raise ValueError(error_msg)
            elif isinstance(repo_or_spec, Dependency):
                unresolved_dependencies = [(repo_or_spec, 0)]
            elif isinstance(repo_or_spec, Package):
                unupdated_packages = [(repo_or_spec, 0)]
            else:
                error_msg = "repo_or_spec must be either a Package, Dependency, or SourceRepository"
                raise TypeError(error_msg)

            t.total = len(unupdated_packages) + len(unresolved_dependencies)

            futures: set[Future[_DependencyResult | _PackageResult]] = set()
            queued: set[Dependency] = {d for d, _ in unresolved_dependencies}
            if max_workers > 1:
                pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="it-depends-resolver")

            def process_updated_package(
                updated_package: Package,
                at_depth: int,
                updated_in_resolvers: set[str],
                was_updated: bool = True,  # noqa: FBT001, FBT002
            ) -> None:
                repo.add(updated_package)
                if not isinstance(updated_package, SourcePackage) and updated_package is not repo_or_spec:
                    if was_updated:
                        cache.add(updated_package)
                    for r in updated_in_resolvers:
                        repo.set_updated(updated_package, r)
                        cache.set_updated(updated_package, r)
                if depth_limit < 0 or at_depth < depth_limit:
                    new_deps = {d for d in updated_package.dependencies if d not in queued}
                    unresolved_dependencies.extend((d, at_depth + 1) for d in sorted(new_deps))
                    t.total += len(new_deps)
                    queued.update(new_deps)

            def process_resolution(
                dep: Dependency,
                packages: Iterable[Package],
                at_depth: int,
                already_cached: bool = False,  # noqa: FBT001, FBT002
            ) -> None:
                """Process a dependency resolution."""
                repo.set_resolved(dep)
                packages = list(packages)
                if not already_cached and cache is not None and dep is not repo_or_spec:
                    cache.set_resolved(dep)
                    cache.extend(packages)
                unupdated_packages.extend((p, at_depth) for p in packages)
                t.total += len(packages)

            while unresolved_dependencies or unupdated_packages or futures:
                # while there are more unresolved dependencies, unupdated packages,
                # or concurrent jobs that are still running:

                reached_fixed_point = cache is None
                while not reached_fixed_point:
                    reached_fixed_point = True

                    # loop through the unupdated packages and see if any are cached:
                    not_updated: list[tuple[Package, int]] = []
                    was_updatable = False
                    for package, depth in unupdated_packages:
                        for resolver in resolvers():
                            if resolver.can_update_dependencies(package):
                                was_updatable = True
                                if not cache or not cache.was_updated(package, resolver.name):
                                    not_updated.append((package, depth))
                                    break
                        else:
                            if was_updatable:
                                # every resolver that could have updated this package did update it in the cache
                                try:
                                    # retrieve the package from the cache
                                    if not cache:
                                        break
                                    package = next(iter(cache.match(package)))  # noqa: PLW2901
                                except StopIteration:
                                    pass
                            process_updated_package(package, depth, updated_in_resolvers=set())
                            t.update(1)

                    if unupdated_packages != not_updated:
                        reached_fixed_point = False
                        unupdated_packages = not_updated

                    # loop through the unresolved deps and see if any are cached:
                    not_cached: list[tuple[Dependency, int]] = []
                    for dep, depth in unresolved_dependencies:
                        if dep is not repo_or_spec and cache and cache.was_resolved(dep):
                            matches = cache.match(dep)
                            process_resolution(dep, matches, depth, already_cached=True)
                            t.update(1)
                        else:
                            not_cached.append((dep, depth))
                    if unresolved_dependencies != not_cached:
                        reached_fixed_point = False
                        unresolved_dependencies = not_cached

                if max_workers <= 1:
                    # don't use concurrency
                    if unupdated_packages:
                        t.update(1)
                        pkg_result = _update_package(*unupdated_packages[0])
                        unupdated_packages = unupdated_packages[1:]
                        process_updated_package(
                            pkg_result.package,
                            pkg_result.depth,
                            pkg_result.updated_in_resolvers,
                            pkg_result.was_updated,
                        )
                    if unresolved_dependencies:
                        t.update(1)
                        dep_result = _process_dep(*unresolved_dependencies[0])
                        unresolved_dependencies = unresolved_dependencies[1:]
                        process_resolution(dep_result.dep, dep_result.packages, dep_result.depth)
                else:
                    # new_jobs is the number of new concurrent resolutions we can start without exceeding max_workers
                    new_jobs = max_workers - len(futures)
                    # create `new_jobs` package update jobs:
                    futures |= {
                        pool.submit(_update_package, package, depth) for package, depth in unupdated_packages[:new_jobs]
                    }
                    unupdated_packages = unupdated_packages[new_jobs:]
                    new_jobs = max_workers - len(futures)
                    # create `new_jobs` new resolution jobs:
                    futures |= {
                        pool.submit(_process_dep, dep, depth) for dep, depth in unresolved_dependencies[:new_jobs]
                    }
                    unresolved_dependencies = unresolved_dependencies[new_jobs:]
                    if futures:
                        done, futures = wait(futures, return_when=FIRST_COMPLETED)
                        for finished in done:
                            t.update(1)
                            result = finished.result()
                            if isinstance(result, _PackageResult):
                                process_updated_package(
                                    result.package,
                                    result.depth,
                                    result.updated_in_resolvers,
                                    result.was_updated,
                                )
                            elif isinstance(result, _DependencyResult):
                                process_resolution(result.dep, result.packages, result.depth)
                            else:
                                error_msg = f"Unexpected future result: {result!r}"
                                raise NotImplementedError(error_msg)

    except KeyboardInterrupt:
        if sys.stderr.isatty() and sys.stdin.isatty():
            try:
                while True:
                    sys.stderr.write("Would you like to output the partial results? [Yn] ")
                    choice = input().lower()
                    if choice in {"", "y"}:
                        return repo
                    if choice == "n":
                        sys.exit(1)
            except KeyboardInterrupt:
                sys.exit(1)
        raise
    return repo

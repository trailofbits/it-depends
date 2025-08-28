"""Dependency resolution module for managing package dependencies."""

from __future__ import annotations

import logging
import sys
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import suppress
from multiprocessing import cpu_count
from typing import TYPE_CHECKING

from tqdm import tqdm

from .cache import PackageCache, PackageRepository
from .models import Dependency, Package
from .repository import SourceRepository
from .resolver import PartialResolution, resolvers
from .sbom import SBOM

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from .models import Dependency, Package

logger = logging.getLogger(__name__)


class _DependencyResult:
    """Result of dependency resolution."""

    def __init__(self, dep: Dependency, packages: list[Package], depth: int) -> None:
        """Initialize dependency result."""
        self.dep: Dependency = dep
        self.packages: list[Package] = packages
        self.depth: int = depth


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
            except Exception:  # noqa: S110, BLE001
                pass
    return _PackageResult(
        package=package,
        was_updated=len(uir) > 0,
        updated_in_resolvers=uir,
        depth=depth,
    )


def _resolve_dependency(dep: Dependency, depth: int) -> _DependencyResult:
    """Resolve a dependency to packages."""
    for resolver in resolvers():
        if resolver.can_resolve_from_source(dep):
            try:
                packages = list(resolver.resolve(dep))
                return _DependencyResult(dep=dep, packages=packages, depth=depth)
            except Exception:  # noqa: S110, BLE001
                pass
    return _DependencyResult(dep=dep, packages=[], depth=depth)


def resolve_sbom(root_package: Package, packages: PackageRepository, *, order_ascending: bool = True) -> Iterator[SBOM]:  # noqa: C901
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
        current = stack.pop()
        if current in history:
            continue
        history.add(current)

        if current.is_complete:
            yield SBOM(
                dependencies=current.dependencies(),
                root_packages=current.packages(),
            )
            continue

        for package in current.packages():
            if not package.dependencies:
                continue

            for dep in package.dependencies:
                if dep in current:
                    continue

                try:
                    packages_for_dep = list(resolve(dep, packages=packages))
                    if packages_for_dep:
                        new_resolution = current.add(packages_for_dep, dep)
                        if new_resolution.is_valid and new_resolution not in history:
                            stack.append(new_resolution)
                except Exception:  # noqa: S110, BLE001
                    pass


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

    if max_workers is None:
        max_workers = cpu_count()

    try:
        with tqdm(desc=f"resolving {repo_or_spec!s}", leave=False, unit=" dependencies") as t:
            if isinstance(repo_or_spec, SourceRepository):
                # Resolve from source repository
                unresolved_dependencies: list[tuple[Dependency, int]] = []
                unupdated_packages: list[tuple[Package, int]] = []
                found_source_package = False
                for resolver in resolvers():
                    if resolver.can_resolve_from_source(repo_or_spec):
                        try:
                            source_package = resolver.resolve_from_source(repo_or_spec, cache)
                            if source_package:
                                found_source_package = True
                                unupdated_packages.append((source_package, 0))
                        except Exception:  # noqa: S110, BLE001
                            pass
                if not found_source_package:
                    error_msg = f"Can not resolve {repo_or_spec}"
                    raise ValueError(error_msg)
            elif hasattr(repo_or_spec, "package"):  # Dependency
                unresolved_dependencies: list[tuple[Dependency, int]] = [(repo_or_spec, 0)]
                unupdated_packages: list[tuple[Package, int]] = []
            elif hasattr(repo_or_spec, "name"):  # Package
                unresolved_dependencies: list[tuple[Dependency, int]] = []
                unupdated_packages: list[tuple[Package, int]] = [(repo_or_spec, 0)]
            else:
                error_msg = "repo_or_spec must be either a Package, Dependency, or SourceRepository"
                raise ValueError(error_msg)

            t.total = len(unupdated_packages) + len(unresolved_dependencies)

            futures: set[Future[_DependencyResult | _PackageResult]] = set()
            queued: set[Dependency] = {d for d, _ in unresolved_dependencies}
            if max_workers > 1:
                pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="it-depends-resolver")

            def process_updated_package(
                updated_package: Package,
                at_depth: int,
                *,
                updated_in_resolvers: set[str],  # noqa: ARG001
                was_updated: bool = True,  # noqa: ARG001
            ) -> None:
                repo.add(updated_package)
                if at_depth < depth_limit or depth_limit < 0:
                    for dep in updated_package.dependencies:
                        if dep not in queued:
                            queued.add(dep)
                            if max_workers > 1:
                                futures.add(pool.submit(_resolve_dependency, dep, at_depth + 1))
                            else:
                                result = _resolve_dependency(dep, at_depth + 1)
                                new_deps = {d for d, _ in result.packages}
                                queued.update(new_deps)

            def process_resolution(
                dep: Dependency,
                packages: Iterable[Package],
                at_depth: int,
                *,
                already_cached: bool = False,  # noqa: ARG001
            ) -> None:
                """Process a dependency resolution."""
                repo.set_resolved(dep)
                packages = list(packages)
                for package in packages:
                    if package not in repo:
                        repo.add(package)
                        if at_depth < depth_limit or depth_limit < 0:
                            if max_workers > 1:
                                futures.add(pool.submit(_update_package, package, at_depth + 1))
                            else:
                                result = _update_package(package, at_depth + 1)
                                process_updated_package(
                                    result.package,
                                    result.depth,
                                    updated_in_resolvers=result.updated_in_resolvers,
                                    was_updated=result.was_updated,
                                )

            while unresolved_dependencies or unupdated_packages:
                if max_workers > 1 and futures:
                    done, futures = wait(futures, return_when=FIRST_COMPLETED)
                    for future in done:
                        try:
                            result = future.result()
                            if isinstance(result, _DependencyResult):
                                process_resolution(result.dep, result.packages, result.depth)
                            elif isinstance(result, _PackageResult):
                                process_updated_package(
                                    result.package,
                                    result.depth,
                                    updated_in_resolvers=result.updated_in_resolvers,
                                    was_updated=result.was_updated,
                                )
                            else:
                                error_msg = f"Unexpected future result: {result!r}"
                                raise NotImplementedError(error_msg)  # noqa: TRY301
                        except Exception:  # noqa: S110, PERF203, BLE001
                            pass

                # loop through the unupdated packages and see if any are cached:
                not_updated: list[tuple[Package, int]] = []
                was_updatable = False
                for package, depth in unupdated_packages:
                    if cache and cache.was_updated(package):
                        was_updatable = True
                        if was_updatable:
                            # every resolver that could have updated this package did update it in the cache

                            with suppress(StopIteration):
                                # retrieve the package from the cache
                                cached_package = next(iter(cache.match(package)))
                            process_updated_package(cached_package, depth, updated_in_resolvers=set())
                            t.update(1)
                        else:
                            not_updated.append((package, depth))
                    else:
                        not_updated.append((package, depth))
                unupdated_packages = not_updated

                # loop through the unresolved deps and see if any are cached:
                not_cached: list[tuple[Dependency, int]] = []
                for dep, depth in unresolved_dependencies:
                    if dep is not repo_or_spec and cache.was_resolved(dep):
                        packages = cache.match(dep)
                        process_resolution(dep, packages, depth, already_cached=True)
                        t.update(1)
                    else:
                        not_cached.append((dep, depth))
                unresolved_dependencies = not_cached

                if not unresolved_dependencies and not unupdated_packages:
                    break

            if max_workers > 1:
                pool.shutdown(wait=True)

    except KeyboardInterrupt:
        if max_workers > 1:
            pool.shutdown(wait=False)
        if hasattr(sys, "stdin") and sys.stdin.isatty():
            try:
                sys.stderr.write("Would you like to output the partial results? [Yn] ")
                choice = input().lower()
                if choice in {"", "y"}:
                    return repo
                if choice == "n":
                    raise
            except (EOFError, NameError):
                pass
        raise

    return repo

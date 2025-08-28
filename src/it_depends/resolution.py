import logging
import sys
from collections.abc import Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from multiprocessing import cpu_count
from typing import TYPE_CHECKING, List, Optional, Set, Tuple, Union

from tqdm import tqdm

from .cache import InMemoryPackageCache, PackageCache, PackageRepository
from .repository import SourceRepository
from .resolver import resolvers

if TYPE_CHECKING:
    from .models import Dependency, Package
    from .resolver import PartialResolution

logger = logging.getLogger(__name__)


class _DependencyResult:
    def __init__(self, dep: "Dependency", packages: List["Package"], depth: int):
        self.dep: Dependency = dep
        self.packages: List[Package] = packages
        self.depth: int = depth


def _process_dep(dep: "Dependency", depth: int) -> _DependencyResult:
    return _DependencyResult(dep=dep, packages=list(dep.resolver.resolve(dep)), depth=depth)


class _PackageResult:
    def __init__(
        self,
        package: "Package",
        was_updated: bool,
        updated_in_resolvers: Iterable[str],
        depth: int,
    ):
        self.package: Package = package
        self.was_updated: bool = was_updated
        self.updated_in_resolvers: Set[str] = set(updated_in_resolvers)
        self.depth: int = depth


def _update_package(package: "Package", depth: int) -> _PackageResult:
    old_deps = frozenset(package.dependencies)
    uir: List[str] = []
    for resolver in resolvers():
        if resolver.can_update_dependencies(package):
            package = resolver.update_dependencies(package)
            uir.append(resolver.name)
    return _PackageResult(
        package=package,
        was_updated=package.dependencies != old_deps,
        updated_in_resolvers=uir,
        depth=depth,
    )


def resolve_sbom(root_package: "Package", packages: "PackageRepository", order_ascending: bool = True):
    """Generates SBOMs from packages.

    Args:
        root_package: The root package to generate SBOMs for
        packages: The package repository containing all packages
        order_ascending: If True, prefer older versions; if False, prefer newer versions

    Yields:
        SBOM objects representing different dependency resolutions

    """
    from .sbom import SBOM

    if not root_package.dependencies:
        yield SBOM((), (root_package,))
        return

    logger.info(f"Resolving the {['newest', 'oldest'][order_ascending]} possible SBOM for {root_package.name}")

    stack: List[PartialResolution] = [PartialResolution(packages=(root_package,))]

    history: Set[PartialResolution] = {pr for pr in stack if pr.is_valid}

    while stack:
        pr = stack.pop()
        if pr.is_complete:
            yield SBOM(pr.dependencies(), root_packages=(root_package,))
            continue
        elif not pr.is_valid:
            continue

        for dep, required_by in pr.packages.unsatisfied_dependencies():
            if not PartialResolution(packages=required_by, parent=pr).is_valid:
                continue
            for match in sorted(packages.match(dep), key=lambda p: p.version, reverse=order_ascending):
                next_pr = pr.add(required_by, match)
                if next_pr.is_valid and next_pr not in history:
                    history.add(next_pr)
                    stack.append(next_pr)


def resolve(
    repo_or_spec: Union["Package", "Dependency", SourceRepository],
    cache: Optional[PackageCache] = None,
    depth_limit: int = -1,
    repo: Optional[PackageRepository] = None,
    max_workers: Optional[int] = None,
) -> PackageRepository:
    """Resolves the dependencies for a package, dependency, or source repository.

    If depth_limit is negative (the default), recursively resolve all dependencies.
    If depth_limit is greater than zero, only recursively resolve dependencies to that depth.
    max_workers controls the number of spawned threads, if None cpu_count is used.
    """
    if depth_limit == 0:
        return PackageRepository()

    if max_workers is None:
        try:
            max_workers = cpu_count()
        except NotImplementedError:
            max_workers = 5

    if repo is None:
        repo = PackageRepository()

    if cache is None:
        cache = InMemoryPackageCache()  # Some resolvers may use it to save temporary results

    try:
        with (
            cache,
            tqdm(desc=f"resolving {repo_or_spec!s}", leave=False, unit=" dependencies") as t,
        ):
            if hasattr(repo_or_spec, "package"):  # Dependency
                unresolved_dependencies: List[Tuple[Dependency, int]] = [(repo_or_spec, 0)]
                unupdated_packages: List[Tuple[Package, int]] = []
            elif hasattr(repo_or_spec, "name"):  # Package
                unresolved_dependencies = []
                unupdated_packages = [(repo_or_spec, 0)]
            elif hasattr(repo_or_spec, "path"):  # SourceRepository
                # repo_or_spec is a SourceRepository
                unresolved_dependencies = []
                unupdated_packages = []
                found_source_package = False
                for resolver in resolvers():
                    if resolver.can_resolve_from_source(repo_or_spec):
                        source_package = resolver.resolve_from_source(repo_or_spec, cache=cache)
                        if source_package is None:
                            continue
                        found_source_package = True
                        unupdated_packages.append((source_package, 0))
                if not found_source_package:
                    raise ValueError(f"Can not resolve {repo_or_spec}")
            else:
                raise ValueError("repo_or_spec must be either a Package, Dependency, or SourceRepository")

            t.total = len(unupdated_packages) + len(unresolved_dependencies)

            futures: Set[Future[Union[_DependencyResult, _PackageResult]]] = set()
            queued: Set[Dependency] = {d for d, _ in unresolved_dependencies}
            if max_workers > 1:
                pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="it-depends-resolver")

            def process_updated_package(
                updated_package: "Package",
                at_depth: int,
                updated_in_resolvers: Set[str],
                was_updated: bool = True,
            ):
                repo.add(updated_package)
                if (
                    not hasattr(updated_package, "source_repo")  # not SourcePackage
                    and updated_package is not repo_or_spec
                ):
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
                dep: "Dependency",
                packages: Iterable["Package"],
                at_depth: int,
                already_cached: bool = False,
            ):
                """This gets called whenever we resolve a new package"""
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
                    not_updated: List[Tuple[Package, int]] = []
                    was_updatable = False
                    for package, depth in unupdated_packages:
                        for resolver in resolvers():
                            if resolver.can_update_dependencies(package):
                                was_updatable = True
                                if not cache.was_updated(package, resolver.name):
                                    not_updated.append((package, depth))
                                    break
                        else:
                            if was_updatable:
                                # every resolver that could have updated this package did update it in the cache
                                try:
                                    # retrieve the package from the cache
                                    package = next(iter(cache.match(package)))
                                except StopIteration:
                                    pass
                            process_updated_package(package, depth, updated_in_resolvers=set())
                            t.update(1)

                    if unupdated_packages != not_updated:
                        reached_fixed_point = False
                        unupdated_packages = not_updated

                    # loop through the unresolved deps and see if any are cached:
                    not_cached: List[Tuple[Dependency, int]] = []
                    for dep, depth in unresolved_dependencies:
                        if dep is not repo_or_spec and cache.was_resolved(dep):
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
                                raise NotImplementedError(f"Unexpected future result: {result!r}")

    except KeyboardInterrupt:
        if sys.stderr.isatty() and sys.stdin.isatty():
            try:
                while True:
                    sys.stderr.write("Would you like to output the partial results? [Yn] ")
                    choice = input().lower()
                    if choice == "" or choice == "y":
                        return repo
                    if choice == "n":
                        sys.exit(1)
            except KeyboardInterrupt:
                sys.exit(1)
        raise
    return repo

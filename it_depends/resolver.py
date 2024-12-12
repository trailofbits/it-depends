import logging
from collections import defaultdict
from logging import getLogger
from typing import Dict, FrozenSet, Iterable, Iterator, List, Optional, Set, Tuple

from semantic_version.base import AllOf, BaseSpec

from .dependencies import Dependency, Package, PackageCache
from .sbom import SBOM

logger = getLogger(__name__)


class CompoundSpec(BaseSpec):
    def __init__(self, *to_combine: BaseSpec):
        super(CompoundSpec, self).__init__(",".join(s.expression for s in to_combine))
        self.clause = AllOf(*(s.clause for s in to_combine))

    @classmethod
    def _parse_to_clause(cls, expression):
        """Converts an expression to a clause."""
        # Placeholder, we actually set self.clause in self.__init__
        return None


class PackageSet:
    def __init__(self):
        self._packages: Dict[Tuple[str, str], Package] = {}
        self._unsatisfied: Dict[Tuple[str, str], Dict[Dependency, Set[Package]]] = \
            defaultdict(lambda: defaultdict(set))
        self.is_valid: bool = True
        self.is_complete: bool = True

    def __eq__(self, other):
        return isinstance(other, PackageSet) and self._packages.values() == other._packages.values()

    def __hash__(self):
        return hash(frozenset(self._packages.values()))

    def __len__(self):
        return len(self._packages)

    def __iter__(self) -> Iterator[Package]:
        yield from self._packages.values()

    def __contains__(self, package: Package) -> bool:
        pkg_spec = (package.name, package.source)
        return pkg_spec in self._packages and self._packages[pkg_spec] == package

    def unsatisfied_dependencies(self) -> Iterator[Tuple[Dependency, FrozenSet[Package]]]:
        for (pkg_name, pkg_source), deps in sorted(
                # try the dependencies with the most options first
                self._unsatisfied.items(),
                key=lambda x: (len(x[1]), x[0])
        ):
            if len(deps) == 0:
                continue
            elif len(deps) == 1:
                dep, packages = next(iter(deps.items()))
            else:
                # there are multiple requirements for the same dependency
                spec = CompoundSpec(*(d.semantic_version for d in deps.keys()))
                dep = Dependency(pkg_name, pkg_source, spec)
                packages = {
                    p
                    for packages in deps.values()
                    for p in packages
                }

            yield dep, frozenset(packages)

    def copy(self) -> "PackageSet":
        ret = PackageSet()
        ret._packages = self._packages.copy()
        ret._unsatisfied = defaultdict(lambda: defaultdict(set))
        for dep_spec, deps in self._unsatisfied.items():
            ret._unsatisfied[dep_spec] = defaultdict(set)
            for dep, packages in deps.items():
                ret._unsatisfied[dep_spec][dep] = set(packages)
                assert all(p in ret for p in packages)
        ret.is_valid = self.is_valid
        ret.is_complete = self.is_complete
        return ret

    def add(self, package: Package):
        pkg_spec = (package.name, package.source)
        if pkg_spec in self._packages and self._packages[pkg_spec].version != package.version:
            self.is_valid = False
        if not self.is_valid:
            return
        self._packages[pkg_spec] = package
        if pkg_spec in self._unsatisfied:
            # there are some existing packages that have unsatisfied dependencies that could be
            # satisfied by this new package
            for dep in list(self._unsatisfied[pkg_spec].keys()):
                if dep.match(package):
                    del self._unsatisfied[pkg_spec][dep]
                    if len(self._unsatisfied[pkg_spec]) == 0:
                        del self._unsatisfied[pkg_spec]
        # add any new unsatisfied dependencies for this package
        for dep in package.dependencies:
            dep_spec = (dep.package, dep.source)
            if dep_spec not in self._packages:
                self._unsatisfied[dep_spec][dep].add(package)
            elif not dep.match(self._packages[dep_spec]):
                self.is_valid = False
                break

        self.is_complete = self.is_valid and len(self._unsatisfied) == 0


class PartialResolution:
    def __init__(self, packages: Iterable[Package] = (), dependencies: Iterable[Package] = (),
                 parent: Optional["PartialResolution"] = None):
        self._packages: FrozenSet[Package] = frozenset(packages)
        self._dependencies: FrozenSet[Package] = frozenset(dependencies)
        self.parent: Optional[PartialResolution] = parent
        if self.parent is not None:
            self.packages: PackageSet = self.parent.packages.copy()
        else:
            self.packages = PackageSet()
        for package in self._packages:
            self.packages.add(package)
            if not self.is_valid:
                break
        if self.is_valid:
            for dep in self._dependencies:
                self.packages.add(dep)
                if not self.is_valid:
                    break

    @property
    def is_valid(self) -> bool:
        return self.packages.is_valid

    @property
    def is_complete(self) -> bool:
        return self.packages.is_complete

    def __contains__(self, package: Package) -> bool:
        return package in self.packages

    def add(self, packages: Iterable[Package], depends_on: Package) -> "PartialResolution":
        return PartialResolution(packages, (depends_on,), parent=self)

    def packages(self) -> Iterator[Package]:
        yield from self.packages

    __iter__ = packages

    def dependencies(self) -> Iterator[Tuple[Package, Package]]:
        pr: Optional[PartialResolution] = self
        while pr is not None:
            for depends_on in sorted(pr._dependencies):
                for package in pr._packages:
                    yield package, depends_on
            pr = pr.parent

    def __len__(self) -> int:
        return len(self.packages)

    def __eq__(self, other):
        return isinstance(other, PartialResolution) and self.packages == other.packages

    def __hash__(self):
        return hash(self.packages)


def resolve_sbom(root_package: Package, packages: PackageCache, order_ascending: bool = True) -> Iterator[SBOM]:
    if not root_package.dependencies:
        yield SBOM((), (root_package,))
        return

    logger.info(f"Resolving the {['oldest', 'newest'][order_ascending]} possible SBOM for {root_package.name}")

    stack: List[PartialResolution] = [
        PartialResolution(packages=(root_package,))
    ]

    history: Set[PartialResolution] = {
        pr for pr in stack
        if pr.is_valid
    }

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
            for match in sorted(
                    packages.match(dep),
                    key=lambda p: p.version,
                    reverse=order_ascending
            ):
                next_pr = pr.add(required_by, match)
                if next_pr.is_valid and next_pr not in history:
                    history.add(next_pr)
                    stack.append(next_pr)

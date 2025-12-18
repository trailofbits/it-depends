"""Dependency graph implementation."""

from __future__ import annotations

import logging

from .graphs import RootedDiGraph
from .models import Dependency, Package, SourcePackage

logger = logging.getLogger(__name__)


class DependencyGraph(RootedDiGraph[Package, SourcePackage]):
    """Dependency graph for packages and source packages."""

    root_type = SourcePackage
    _collapsed: bool = False

    @property
    def source_packages(self) -> set[SourcePackage]:
        """Get all source packages in the graph."""
        return self.roots

    def packages_by_name(self) -> dict[tuple[str, str], set[Package]]:
        """Group packages by (source, name) tuple."""
        ret: dict[tuple[str, str], set[Package]] = {}
        for node in self:
            name = node.source, node.name
            if name not in ret:
                ret[name] = {node}
            else:
                ret[name].add(node)
        return ret

    def collapse_versions(self) -> DependencyGraph:
        """Group all versions of a package into a single node.

        All dependency edges will be grouped into a single edge with a wildcard semantic version.
        """
        if self._collapsed:
            return self
        graph = DependencyGraph()
        package_instances = self.packages_by_name()
        packages_by_name: dict[str, Package] = {}
        # choose the maximum version among all packages of the same name:
        for (package_source, package_name), instances in package_instances.items():
            # convert all of the dependencies to SimpleSpec("*") wildcard versions:

            deps = {
                Dependency(package=dep.package, source=dep.source)
                for instance in instances
                for dep in instance.dependencies
            }
            if len(instances) == 1:
                pkg = next(iter(instances))
            else:
                source_packages_in_instances = self.source_packages & instances
                version = max(p.version for p in instances)
                if source_packages_in_instances:
                    # at least one of the instances is a source package, so make the collapsed package a source package
                    source_repos = {s.source_repo for s in source_packages_in_instances}
                    source_repo = next(iter(source_repos))
                    if len(source_repos) > 1:
                        msg = (
                            f"package {package_source}:{package_name} is provided by multiple source "
                            f"repositories: {', '.join(map(str, source_repos))}. "
                            f"Collapsing to {source_repo}."
                        )
                        logger.warning(msg)

                    pkg = SourcePackage(
                        name=package_name,
                        version=version,
                        source_repo=source_repo,
                        source=package_source,
                        dependencies=deps,
                    )
                else:
                    pkg = Package(
                        name=package_name,
                        version=version,
                        source=package_source,
                        dependencies=deps,
                    )
            packages_by_name[pkg.full_name] = pkg
            graph.add_node(pkg)
        for pkg in graph:
            for dep in pkg.dependencies:
                if dep.package_full_name in packages_by_name:
                    graph.add_edge(pkg, packages_by_name[dep.package_full_name], dependency=dep)
        graph._collapsed = True
        return graph

    def distance_to(self, graph: RootedDiGraph[Package, SourcePackage], *, normalize: bool = False) -> float:
        """Calculate distance to another graph."""
        if not self._collapsed:
            return self.collapse_versions().distance_to(graph, normalize=normalize)
        if not self.source_packages:
            # use our roots instead:
            compare_from: RootedDiGraph[Package, Package] = self.find_roots()
        else:
            compare_from = self
        if isinstance(graph, DependencyGraph):
            compare_to: RootedDiGraph[Package, Package] = graph.collapse_versions()
        else:
            compare_to = graph
        if not compare_to.roots:
            compare_to = compare_to.find_roots()
        if compare_from is self:
            return super().distance_to(compare_to, normalize=normalize)
        return compare_from.distance_to(compare_to, normalize=normalize)

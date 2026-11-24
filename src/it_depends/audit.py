"""Vulnerability auditing functionality."""

from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, ClassVar

from requests import post

from it_depends.reporting import Severity, Task, diagnostic, in_context

if TYPE_CHECKING:
    from collections.abc import Iterable

from it_depends.dependencies import Package, PackageRepository, Vulnerability


class OSVVulnerability(Vulnerability):
    """Represents a vulnerability from the OSV project."""

    """Additional keys available from the OSV Vulnerability db."""
    EXTRA_KEYS: ClassVar[list[str]] = [
        "published",
        "modified",
        "withdrawn",
        "related",
        "package",
        "details",
        "affects",
        "affected",
        "references",
        "severity",
        "database_specific",
        "ecosystem_specific",
    ]

    def __init__(self, osv_dict: dict) -> None:
        """Initialize OSV vulnerability from dictionary."""
        # Get the first available information as summary (N/A if none)
        summary = osv_dict.get("summary", "") or osv_dict.get("details", "") or "N/A"
        super().__init__(osv_dict["id"], osv_dict.get("aliases", []), summary)

        # Inherit all other attributes
        for k in OSVVulnerability.EXTRA_KEYS:
            setattr(self, k, osv_dict.get(k))

    @classmethod
    def from_osv_dict(cls, d: dict) -> OSVVulnerability:
        """Create OSV vulnerability from dictionary."""
        return OSVVulnerability(d)


class VulnerabilityProvider(ABC):
    """Interface of a vulnerability provider."""

    @abstractmethod
    def query(self, pkg: Package) -> Iterable[Vulnerability]:
        """Query the vulnerability provider for vulnerabilities in package."""
        raise NotImplementedError


class OSVProject(VulnerabilityProvider):
    """OSV project vulnerability provider."""

    QUERY_URL = "https://api.osv.dev/v1/query"

    def query(self, pkg: Package) -> Iterable[OSVVulnerability]:
        """Query the OSV project for vulnerabilities in package."""
        q = {"version": str(pkg.version), "package": {"name": pkg.name}}
        r = post(OSVProject.QUERY_URL, json=q, timeout=30)
        r.raise_for_status()
        return map(OSVVulnerability.from_osv_dict, r.json().get("vulns", []))


def vulnerabilities(repo: PackageRepository, nworkers: int | None = None) -> PackageRepository:
    """Enrich packages with vulnerability information."""

    def _get_vulninfo(pkg: Package) -> tuple[Package, frozenset[Vulnerability]]:
        """Enrich a Package with vulnerability information."""
        ret = OSVProject().query(pkg)
        # Do not modify pkg here to ensure no concurrent
        # modifications, instead return and let the main
        # thread handle the updates.
        return (pkg, frozenset(ret))

    with (
        ThreadPoolExecutor(max_workers=nworkers) as executor,
        Task("Checking for vulnerabilities", unit="packages").running() as t,
    ):
        futures = {executor.submit(in_context(_get_vulninfo, pkg)): pkg for pkg in repo}
        t.set_total(len(futures))

        for future in as_completed(futures):
            try:
                t.advance(1)
                pkg, vulns = future.result()
            except Exception:  # noqa: BLE001, PERF203 - Report each failed lookup and continue.
                diagnostic(Severity.ERROR, __name__, "Failed to retrieve vulnerability information", exception=True)
            else:
                pkg.update_vulnerabilities(vulns)

    return repo

from abc import ABC
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from requests import post
from tqdm import tqdm
from typing import Dict, FrozenSet, Iterable, List, Union, Tuple

from .dependencies import Package, PackageRepository, Vulnerability

logger = logging.getLogger(__name__)


class OSVVulnerability(Vulnerability):
    """Represents a vulnerability from the OSV project"""

    """Additional keys available from the OSV Vulnerability db."""
    EXTRA_KEYS = [
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

    def __init__(self, osv_dict: Dict):
        # Get the first available information as summary (N/A if none)
        summary = osv_dict.get("summary", "") or osv_dict.get("details", "") or "N/A"
        super().__init__(osv_dict["id"], osv_dict.get("aliases", []), summary)

        # Inherit all other attributes
        for k in OSVVulnerability.EXTRA_KEYS:
            setattr(self, k, osv_dict.get(k, None))

    @classmethod
    def from_osv_dict(cls, d: Dict):
        return OSVVulnerability(d)


class VulnerabilityProvider(ABC):
    """Interface of a vulnerability provider."""

    def query(self, pkg: Package) -> Iterable[Vulnerability]:
        """Queries the vulnerability provider for vulnerabilities in pkg"""
        raise NotImplementedError()


class OSVProject(VulnerabilityProvider):
    """OSV project vulnerability provider"""

    QUERY_URL = "https://api.osv.dev/v1/query"

    def query(self, pkg: Package) -> Iterable[OSVVulnerability]:
        """Queries the OSV project for vulnerabilities in Package pkg"""
        q = {"version": str(pkg.version), "package": {"name": pkg.name}}
        r = post(OSVProject.QUERY_URL, json=q).json()
        return map(OSVVulnerability.from_osv_dict, r.get("vulns", []))


def vulnerabilities(repo: PackageRepository, nworkers=None) -> PackageRepository:
    def _get_vulninfo(pkg: Package) -> Tuple[Package, FrozenSet[Vulnerability]]:
        """Enrich a Package with vulnerability information"""
        ret = OSVProject().query(pkg)
        # Do not modify pkg here to ensure no concurrent
        # modifications, instead return and let the main
        # thread handle the updates.
        return (pkg, frozenset({vuln: vuln for vuln in ret}))

    with ThreadPoolExecutor(max_workers=nworkers) as executor, tqdm(
        desc="Checking for vulnerabilities", leave=False, unit=" packages"
    ) as t:
        futures = {executor.submit(_get_vulninfo, pkg): pkg for pkg in repo}
        t.total = len(futures)

        for future in as_completed(futures):
            try:
                t.update(1)
                pkg, vulns = future.result()
            except Exception as exc:
                logger.error(
                    "Failed to retrieve vulnerability information. " "Exception: {}".format(exc)
                )
            else:
                pkg.update_vulnerabilities(vulns)

    return repo

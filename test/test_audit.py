import logging
import secrets
import string
import threading
import time
from unittest import TestCase
from unittest.mock import Mock, patch

from it_depends import audit
from it_depends.dependencies import InMemoryPackageCache, Package, Vulnerability

logger = logging.getLogger(__name__)


def _rand_str(n: int) -> str:
    """Returns a random string of length n (upper, lower and digits)"""
    return "".join(secrets.choice(string.ascii_lowercase + string.ascii_uppercase + string.digits) for i in range(n))


def _version_str() -> str:
    """Returns a typical version string (x.y.z)"""
    return f"{secrets.randbelow(30)}.{secrets.randbelow(5)}.{secrets.randbelow(9)}"


def _random_package() -> Package:
    """Returns a package of random name, version and source"""
    return Package(_rand_str(10), _version_str(), _rand_str(5))


def _random_packages(num_packages: int) -> InMemoryPackageCache:
    """Returns PackacgeCache populated with num_package random Packages"""
    packages = InMemoryPackageCache()
    list(map(packages.add, [_random_package() for i in range(num_packages)]))
    return packages


def _random_vulnerability() -> Vulnerability:
    """Create a random vulnerability"""
    return Vulnerability(
        _rand_str(10),
        [_rand_str(3) for _ in range(secrets.randbelow(7)) if secrets.randbelow(100) < 90],  # noqa: PLR2004
        _rand_str(secrets.randbelow(10)),
    )


def _random_vulnerabilities(max_count: int) -> list[Vulnerability]:
    """Return up to max_count vulnerabilities"""
    return [_random_vulnerability() for _ in range(secrets.randbelow(max_count))]


class TestAudit(TestCase):
    def setUp(self) -> None:
        # To be able to repeat a failing test the seed for random is logged
        seed = int(time.time())
        secrets.seed(seed)
        logger.warning("Using seed: %s", seed)

    @patch("it_depends.audit.post")
    def test_nopackages_no_requests(self, mock_post: Mock) -> None:
        packages = _random_packages(0)
        ret = audit.vulnerabilities(packages)
        assert ret == packages
        mock_post.assert_not_called()

    @patch("it_depends.audit.post")
    def test_valid_limited_info_response(self, mock_post: Mock) -> None:
        """Ensures that a single vuln with the minimum amount of info we require works"""
        packages = _random_packages(1)
        mock_post().json.return_value = {"vulns": [{"id": "123"}]}
        ret = audit.vulnerabilities(packages)

        pkg = next(p for p in ret)
        vuln = next(v for v in pkg.vulnerabilities)  # Assume one vulnerability
        assert vuln.id == "123"
        assert len(vuln.aliases) == 0
        assert vuln.summary == "N/A"

    @patch("it_depends.audit.post")
    def test_no_vulns_can_be_handled(self, mock_post: Mock) -> None:
        """No vulnerability info can still be handled"""
        packages = _random_packages(1)
        mock_post().json.return_value = {}
        ret = audit.vulnerabilities(packages)
        assert all(len(p.vulnerabilities) == 0 for p in ret)

    @patch("it_depends.audit.post")
    def test_handles_ten_thousand_requests(self, mock_post: Mock) -> None:
        """Constructs ten thousand random packages and maps random vulnerabilities to the packages.
        Ensures that the vulnerability information received from OSV is reflected in the Packages"""

        # Create 10k random packages (name, version, source)
        packages = _random_packages(10000)

        # For each of the packages map 0 or more vulnerabilities
        package_vuln = {(pkg.name, str(pkg.version)): _random_vulnerabilities(10) for pkg in packages}

        # Mocks the json-request to OSV, returns whatever info is in the package_vuln-map
        def _osv_response(_, json: dict) -> Mock:  # noqa: ANN001
            m = Mock()
            key = (json["package"]["name"], json["version"])
            if key in package_vuln:
                m.json.return_value = {"vulns": [x.to_obj() for x in package_vuln[key]]}
            else:
                m.json.return_value = {}
            return m

        mock_post.side_effect = _osv_response

        # Query all packages for vulnerabilities, ensure that each package received vulnerabilitiy
        # info as stated in the package_vuln-map created earlier.
        for pkg in audit.vulnerabilities(packages):
            pkgvuln = sorted(pkg.vulnerabilities)
            expectedvuln = sorted(package_vuln[(pkg.name, str(pkg.version))])

            assert pkgvuln == expectedvuln

    @patch("it_depends.audit.post")
    def test_exceptions_are_logged_and_isolated(self, mock_post: Mock) -> None:
        """Ensure that if exceptions happen during vulnerability querying they do not kill execution.
        They shall still be logged."""
        packages = _random_packages(100)
        lock = threading.Lock()
        counter = 0

        def _osv_response(_, json: dict) -> Mock:  # noqa: ANN001, ARG001
            nonlocal counter
            m = Mock()
            m.json.return_value = {}
            with lock:
                counter += 1
                if counter % 2 == 0:
                    msg = "Ouch."
                    raise Exception(msg)  # noqa: TRY002
            return m

        mock_post.side_effect = _osv_response

        assert len(audit.vulnerabilities(packages)) == 100  # noqa: PLR2004

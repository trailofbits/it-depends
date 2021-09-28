import threading
from it_depends.dependencies import InMemoryPackageCache, Package, Vulnerability
from it_depends import audit

import logging
import random
import string
import time
from unittest import TestCase
from unittest.mock import Mock, patch


logger = logging.getLogger(__name__)


def _rand_str(n):
    """Returns a random string of length n (upper, lower and digits)"""
    return ''.join(random.choice(string.ascii_lowercase +
                   string.ascii_uppercase + string.digits)
                   for i in range(n))


def _version_str():
    """Returns a typical version string (x.y.z)"""
    return f"{random.randint(0, 30)}.{random.randint(0,5)}." \
           f"{random.randint(0, 9)}"


def _random_package():
    """Returns a package of random name, version and source"""
    return Package(_rand_str(10), _version_str(), _rand_str(5))


def _random_packages(num_packages):
    """Returns PackacgeCache populated with num_package random Packages"""
    packages = InMemoryPackageCache()
    list(map(packages.add, [_random_package() for i in range(num_packages)]))
    return packages


def _random_vulnerability():
    """Create a random vulnerability"""
    return Vulnerability(_rand_str(10),
                         [_rand_str(3) for i in range(random.randint(0, 7)) if
                          random.randint(0, 100) < 90],
                         _rand_str(random.randint(0, 10)))


def _random_vulnerabilities(max_count):
    """Return up to max_count vulnerabilities"""
    return [_random_vulnerability() for x in range(random.randint(0, max_count))]


class TestAudit(TestCase):
    def setUp(self):
        # To be able to repeat a failing test the seed for random is logged
        seed = int(time.time())
        random.seed(seed)
        logger.warning(f"Using seed: {seed}")

    @patch('it_depends.audit.post')
    def test_nopackages_no_requests(self, mock_post):
        packages = _random_packages(0)
        ret = audit.vulnerabilities(packages)
        self.assertEqual(ret, packages)
        mock_post.assert_not_called()

    @patch('it_depends.audit.post')
    def test_valid_limited_info_response(self, mock_post):
        """Ensures that a single vuln with the minimum amount of info we require works"""
        packages = _random_packages(1)
        mock_post().json.return_value = {"vulns": [{"id": "123"}]}
        ret = audit.vulnerabilities(packages)

        pkg = next(p for p in ret)
        vuln = next(v for v in pkg.vulnerabilities)  # Assume one vulnerability
        self.assertEqual(vuln.id, "123")
        self.assertEqual(len(vuln.aliases), 0)
        self.assertEqual(vuln.summary, "N/A")

    @patch('it_depends.audit.post')
    def test_no_vulns_can_be_handled(self, mock_post):
        """No vulnerability info can still be handled"""
        packages = _random_packages(1)
        mock_post().json.return_value = {}
        ret = audit.vulnerabilities(packages)
        self.assertTrue(all(map(lambda p: len(p.vulnerabilities) == 0, ret)))

    @patch('it_depends.audit.post')
    def test_handles_ten_thousand_requests(self, mock_post):
        """Constructs ten thousand random packages and maps random vulnerabilities to the packages.
        Ensures that the vulnerability information received from OSV is reflected in the Packages"""

        # Create 10k random packages (name, version, source)
        packages = _random_packages(10000)

        # For each of the packages map 0 or more vulnerabilities
        package_vuln = {(pkg.name, str(pkg.version)): _random_vulnerabilities(10) for pkg in packages}

        # Mocks the json-request to OSV, returns whatever info is in the package_vuln-map
        def _osv_response(_, json):
            m = Mock()
            key = (json["package"]["name"], json["version"])
            if key in package_vuln:
                m.json.return_value = {"vulns": list(map(lambda x: x.to_obj(), package_vuln[key]))}
            else:
                m.json.return_value = {}
            return m

        mock_post.side_effect = _osv_response

        # Query all packages for vulnerabilities, ensure that each package received vulnerabilitiy
        # info as stated in the package_vuln-map created earlier.
        for pkg in audit.vulnerabilities(packages):
            pkgvuln = sorted(pkg.vulnerabilities)
            expectedvuln = sorted(package_vuln[(pkg.name, str(pkg.version))])

            self.assertListEqual(pkgvuln, expectedvuln)

    @patch('it_depends.audit.post')
    def test_exceptions_are_logged_and_isolated(self, mock_post):
        """Ensure that if exceptions happen during vulnerability querying they do not kill execution.
        They shall still be logged."""
        packages = _random_packages(100)
        lock = threading.Lock()
        counter = 0

        def _osv_response(_, json):
            nonlocal counter
            m = Mock()
            m.json.return_value = {}
            with lock:
                counter += 1
                if counter % 2 == 0:
                    raise Exception("Ouch.")
            return m
        mock_post.side_effect = _osv_response

        self.assertEqual(len(audit.vulnerabilities(packages)), 100)

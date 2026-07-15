import json
import re
import zipfile
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from unittest import TestCase

import pytest
import requests

from it_depends.dependencies import (
    Dependency,
    Package,
    PackageRepository,
    SimpleSpec,
    SourceRepository,
    resolve,
    resolver_by_name,
    resolvers,
)

IT_DEPENDS_DIR: Path = Path(__file__).absolute().parent.parent
TESTS_DIR: Path = Path(__file__).absolute().parent
REPOS_FOLDER = TESTS_DIR / "repos"


class TestResolvers(TestCase):
    maxDiff = None

    @pytest.mark.integration
    def test_resolvers(self) -> None:
        """We see all known resolvers
        caveat: Iff an unknown resolver was defined by another test it will appear here
        """
        resolver_names = {resolver.name for resolver in resolvers()}
        assert resolver_names == {"cargo", "ubuntu", "autotools", "go", "cmake", "npm", "pip"}
        assert resolvers() == {resolver_by_name(name) for name in resolver_names}

    @pytest.mark.integration
    def test_objects(self) -> None:
        # To/From string for nicer output and ergonomics
        assert str(Dependency.from_string("pip:cvedb@*")) == "pip:cvedb@*"
        assert str(Package.from_string("pip:cvedb@0.0.1")) == "pip:cvedb@0.0.1"

        # Basic Dependency object handling
        dep = Dependency.from_string("pip:cvedb@*")
        assert dep.source == "pip"
        assert dep.package == "cvedb"
        assert dep.semantic_version == SimpleSpec("*")
        assert Dependency(source="pip", package="cvedb", semantic_version=SimpleSpec("*")) == dep

        # Dependency match
        solution = Package(source="pip", name="cvedb", version="0.0.1")
        assert dep.match(solution) is True
        dep2 = Dependency.from_string("pip:cvedb@<0.2.1")
        assert dep2.match(Package.from_string("pip:cvedb@0.2.0")) is True
        assert dep2.match(Package.from_string("pip:cvedb@0.2.1")) is False

    def _assert_determinism(self, dep_str: str, num_attempts: int = 3, depth_limit: int = -1) -> None:
        """Assert that resolving a dependency yields the same package set each time.

        Each attempt resolves live with a fresh cache, so transitive (and threaded) resolution is
        re-exercised every attempt, not just the root. Comparison is version-agnostic -- by
        ``(source, name)`` -- so upstream version drift between attempts (a version published or
        yanked mid-run) does not flake the test, while genuine it-depends nondeterminism, which
        would add or drop a package entirely, is still caught.
        """
        dep = Dependency.from_string(dep_str)

        def identities() -> set[tuple[str, str]]:
            return {(p.source, p.name) for p in resolve(dep, depth_limit=depth_limit)}

        baseline = identities()
        for attempt in range(1, num_attempts):
            assert identities() == baseline, f"Results differed on attempt {attempt + 1} resolving {dep}"

    @pytest.mark.integration
    def test_determinism_pip(self) -> None:
        self._assert_determinism("pip:cvedb@*")

    @pytest.mark.integration
    def test_determinism_ubuntu(self) -> None:
        self._assert_determinism("ubuntu:libc6@*")

    @pytest.mark.integration
    def test_determinism_cargo(self) -> None:
        self._assert_determinism("cargo:rand_core@0.6.2", num_attempts=2, depth_limit=1)

    @pytest.mark.integration
    def test_determinism_npm(self) -> None:
        self._assert_determinism("npm:crypto-js@4.0.0")


class SmokeTest:
    def __init__(self, user_name: str, repo_name: str, commit: str) -> None:
        self.user_name: str = user_name
        self.repo_name: str = repo_name
        self.commit: str = commit

        self.url: str = f"https://github.com/{user_name}/{repo_name}/archive/{commit}.zip"
        self._snapshot_folder: Path = REPOS_FOLDER / (repo_name + "-" + commit)
        self._snapshot_zip: Path = self._snapshot_folder.with_suffix(".zip")

        self.expected_json: Path = REPOS_FOLDER / f"{repo_name}.expected.json"
        self.actual_json: Path = REPOS_FOLDER / f"{repo_name}.actual.json"

    @property
    def snapshot_folder(self) -> Path:
        if not self._snapshot_folder.exists():
            if not self.url.startswith(("http://", "https://")):
                msg = "Invalid URL scheme: %s"
                raise ValueError(msg % self.url)
            response = requests.get(self.url, stream=True, timeout=10)
            response.raise_for_status()
            with self._snapshot_zip.open("wb") as f:
                f.write(response.content)
            with zipfile.ZipFile(self._snapshot_zip, "r") as zip_ref:
                zip_ref.extractall(REPOS_FOLDER)
        return self._snapshot_folder

    @property
    def source_repo(self) -> SourceRepository:
        return SourceRepository(self.snapshot_folder)

    def run(self) -> PackageRepository:
        return resolve(self.source_repo)

    def __hash__(self) -> int:
        return hash((self.user_name, self.repo_name, self.commit))

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, SmokeTest)
            and self.user_name == other.user_name
            and self.repo_name == other.repo_name
            and self.commit == other.commit
        )


SMOKE_TESTS: set[SmokeTest] = set()

ResolutionObj = dict[str, dict[str, dict[str, str | bool | list[str] | dict[str, str]]]]

# Package names from these ecosystems are stable across version churn. Native/distro names
# (e.g. ``ubuntu:libboost-filesystem1.71-dev``) embed soname versions that drift with the base
# image, so they are deliberately not matched by name.
STABLE_NAME_SOURCES = frozenset({"pip", "npm", "cargo", "go"})

_PIP_NAME_SEP = re.compile(r"[-_.]+")


def _normalize_key(key: str) -> str:
    """Normalize a ``source:name`` key so pip name-spelling drift is not read as a change.

    pip/PyPI treat runs of ``-``, ``_`` and ``.`` as equivalent and are case-insensitive
    (PEP 503), and johnnydep may return either spelling. Other ecosystems are separator- and
    case-sensitive, so only ``pip`` names are normalized.

    Args:
        key: A ``source:name`` resolution key.

    Returns:
        The key with the pip name lowercased and ``-_.`` runs collapsed to ``-``; unchanged
        for non-pip sources.
    """
    source, sep, name = key.partition(":")
    if sep and source == "pip":
        return f"{source}:{_PIP_NAME_SEP.sub('-', name).lower()}"
    return key


def assert_resolution_invariants(actual: ResolutionObj, expected: ResolutionObj, actual_json: Path) -> None:
    """Assert version-agnostic structural invariants of a resolution against a baseline.

    Exact versions are intentionally ignored because upstream registries yank and retroactively
    edit them. The resolution must be non-empty, retain every source (root) package recorded in
    the baseline, and contain every baseline package from a registry ecosystem
    (:data:`STABLE_NAME_SOURCES`). It may legitimately contain *more* packages than the baseline.

    Args:
        actual: ``PackageRepository.to_obj()`` from the live resolution.
        expected: The committed baseline (``test/repos/<repo>.expected.json``).
        actual_json: Path to the written actual output, surfaced in failure messages.

    Raises:
        AssertionError: If the resolution is empty, drops a baseline root, or is missing a
            baseline package from a registry ecosystem.
    """
    assert actual, f"resolution produced no packages (see {actual_json})"
    actual_names = {_normalize_key(k) for k in actual}

    expected_roots = {
        _normalize_key(name)
        for name, versions in expected.items()
        if any(v.get("is_source_package") for v in versions.values())
    }
    missing_roots = expected_roots - actual_names
    assert not missing_roots, f"resolution dropped source package(s) {sorted(missing_roots)} (see {actual_json})"

    stable = {_normalize_key(name) for name in expected if name.split(":", 1)[0] in STABLE_NAME_SOURCES}
    missing = stable - actual_names
    assert not missing, f"resolution missing expected package(s) {sorted(missing)} (see {actual_json})"


def gh_smoke_test(
    user_name: str, repo_name: str, commit: str
) -> Callable[[Callable[[TestCase, PackageRepository], None]], Callable[[TestCase], None]]:
    smoke_test = SmokeTest(user_name, repo_name, commit)
    SMOKE_TESTS.add(smoke_test)

    def do_smoke_test(func: Callable[[TestCase, PackageRepository], None]) -> Callable[[TestCase], None]:
        @pytest.mark.integration
        @wraps(func)
        def wrapper(self: TestCase) -> None:
            package_list = smoke_test.run()
            result_it_depends = package_list.to_obj()
            with smoke_test.actual_json.open("w") as f:
                f.write(json.dumps(result_it_depends, indent=4, sort_keys=True))

            if not smoke_test.expected_json.exists():
                pytest.skip(
                    f"No baseline at {smoke_test.expected_json}; create it from "
                    f"{smoke_test.actual_json} via test/rebuild_expected_output.py"
                )
            with smoke_test.expected_json.open() as f:
                expected = json.load(f)
            assert_resolution_invariants(result_it_depends, expected, smoke_test.actual_json)

            return func(self, package_list)

        return wrapper

    return do_smoke_test


class TestSmoke(TestCase):
    maxDiff = None

    def setUp(self) -> None:
        REPOS_FOLDER.mkdir(parents=True, exist_ok=True)

    @gh_smoke_test("trailofbits", "cvedb", "7441dc0e238e31829891f85fd840d9e65cb629d8")
    def test_pip(self, package_list: PackageRepository) -> None:
        pass

    @gh_smoke_test("trailofbits", "siderophile", "7bca0f5a73da98550c29032f6a2a170f472ea241")
    def test_cargo(self, package_list: PackageRepository) -> None:
        pass

    @pytest.mark.timeout(1800)
    @gh_smoke_test("bitcoin", "bitcoin", "4a267057617a8aa6dc9793c4d711725df5338025")
    def test_autotools(self, package_list: PackageRepository) -> None:
        pass

    @gh_smoke_test("brix", "crypto-js", "971c31f0c931f913d22a76ed488d9216ac04e306")
    def test_npm(self, package_list: PackageRepository) -> None:
        pass

    # @gh_smoke_test("lifting-bits", "rellic", "9cf73b288a3d0c51d5de7e1060cba8656538596f")
    @pytest.mark.timeout(1800)
    @gh_smoke_test("trailofbits", "pe-parse", "94bd12ac539382c303896f175a1ab16352e65a8f")
    def test_cmake(self, package_list: PackageRepository) -> None:
        pass

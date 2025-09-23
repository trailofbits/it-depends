import json
import logging
import zipfile
from functools import wraps
from pathlib import Path
from typing import Callable
from unittest import TestCase

import requests

from it_depends.dependencies import (
    Dependency,
    InMemoryPackageCache,
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

logger = logging.getLogger(__name__)


class TestResolvers(TestCase):
    maxDiff = None

    def test_resolvers(self) -> None:
        """We see all known resolvers
        caveat: Iff an unknown resolver was defined by another test it will appear here
        """
        resolver_names = {resolver.name for resolver in resolvers()}
        assert resolver_names == {"cargo", "ubuntu", "autotools", "go", "cmake", "npm", "pip"}
        assert resolvers() == {resolver_by_name(name) for name in resolver_names}

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

    def _test_resolver(self, resolver: str, dep: str) -> tuple:
        dep = Dependency.from_string(dep)
        resolver = resolver_by_name(resolver)
        assert dep.resolver is resolver

        solutions = tuple(resolver.resolve(dep))
        assert len(solutions) > 0
        for package in solutions:
            assert package.source == dep.source
            assert package.name == dep.package
            assert dep.semantic_version.match(package.version) is True
            assert dep.match(package) is True
        return solutions

    def test_determinism(self) -> None:
        """Test if a resolver gives the same solution multiple times in a row.

        Half of the attempts will be without a cache, and the second half will use the same cache.

        """
        cache = InMemoryPackageCache()
        to_test: list[tuple[Dependency | SourceRepository, int]] = [
            (Dependency.from_string(dep_name), 5)
            for dep_name in (
                "pip:cvedb@*",
                "ubuntu:libc6@*",
                "cargo:rand_core@0.6.2",
                "npm:crypto-js@4.0.0",
            )
        ]
        # TODO(@evandowning): Uncomment this again. # noqa: FIX002,TD003
        """
        to_test.extend(
            [
                (smoke_test.source_repo, 3)
                for smoke_test in SMOKE_TESTS
                if smoke_test.repo_name in ("bitcoin", "pe-parse")
            ]
        )
        """
        for dep, num_attempts in to_test:
            with self.subTest(msg="Testing the determinism of dep", dep=dep):
                first_result: set[Package] = set()
                for i in range(num_attempts):
                    if i < num_attempts // 2:
                        attempt_cache: InMemoryPackageCache | None = None
                    else:
                        attempt_cache = cache
                    result = set(resolve(dep, cache=attempt_cache))
                    if i == 0:
                        first_result = result
                    else:
                        assert first_result == result, f"Results differed on attempt {i + 1} at resolving {dep}"

    def test_pip(self) -> None:
        self._test_resolver("pip", "pip:cvedb@*")

    def test_ubuntu(self) -> None:
        self._test_resolver("ubuntu", "ubuntu:libc6@*")

    def test_cargo(self) -> None:
        self._test_resolver("cargo", "cargo:rand_core@0.6.2")

    def test_npm(self) -> None:
        self._test_resolver("npm", "npm:crypto-js@4.0.0")


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


def gh_smoke_test(
    user_name: str, repo_name: str, commit: str
) -> Callable[[Callable[[TestCase, PackageRepository], None]], Callable[[TestCase], None]]:
    smoke_test = SmokeTest(user_name, repo_name, commit)
    SMOKE_TESTS.add(smoke_test)

    def do_smoke_test(func: Callable[[TestCase, PackageRepository], None]) -> Callable[[TestCase], None]:
        @wraps(func)
        def wrapper(self: TestCase) -> None:
            package_list = smoke_test.run()
            result_it_depends = package_list.to_obj()
            with smoke_test.actual_json.open("w") as f:
                f.write(json.dumps(result_it_depends, indent=4, sort_keys=True))

            if not smoke_test.expected_json.exists():
                msg = "File %s needs to be created! See %s for the output of the most recent run."
                raise ValueError(msg % (smoke_test.expected_json.absolute(), smoke_test.actual_json.absolute()))
            with smoke_test.expected_json.open() as f:
                expected = json.load(f)
            if result_it_depends != expected:
                logger.warning("See %s for the result of this run.", smoke_test.actual_json.absolute())
            assert result_it_depends == expected

            return func(self, package_list)

        return wrapper

    return do_smoke_test


class TestSmoke(TestCase):
    maxDiff = None

    def setUp(self) -> None:
        REPOS_FOLDER.mkdir(parents=True, exist_ok=True)

    @gh_smoke_test("trailofbits", "cvedb", "7441dc0e238e31829891f85fd840d9e65cb629d8")
    def __test_pip(self, package_list: PackageRepository) -> None:
        pass

    @gh_smoke_test("trailofbits", "siderophile", "7bca0f5a73da98550c29032f6a2a170f472ea241")
    def __test_cargo(self, package_list: PackageRepository) -> None:
        pass

    @gh_smoke_test("bitcoin", "bitcoin", "4a267057617a8aa6dc9793c4d711725df5338025")
    def __test_autotools(self, package_list: PackageRepository) -> None:
        pass

    @gh_smoke_test("brix", "crypto-js", "971c31f0c931f913d22a76ed488d9216ac04e306")
    def __test_npm(self, package_list: PackageRepository) -> None:
        pass

    # @gh_smoke_test("lifting-bits", "rellic", "9cf73b288a3d0c51d5de7e1060cba8656538596f")
    @gh_smoke_test("trailofbits", "pe-parse", "94bd12ac539382c303896f175a1ab16352e65a8f")
    def __test_cmake(self, package_list: PackageRepository) -> None:
        pass

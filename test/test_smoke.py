from functools import wraps
from unittest import TestCase
from pathlib import Path
import os
import json
from typing import Set
import urllib
import zipfile

from it_depends.dependencies import (
    Dependency, InMemoryPackageCache, List, Optional, Package, PackageRepository, resolve, resolver_by_name, resolvers,
    SimpleSpec, SourceRepository, Tuple, Union
)

IT_DEPENDS_DIR: Path = Path(__file__).absolute().parent.parent
TESTS_DIR: Path = Path(__file__).absolute().parent
REPOS_FOLDER = TESTS_DIR / "repos"


class TestResolvers(TestCase):
    maxDiff = None

    def test_resolvers(self):
        """We see all known resolvers
        caveat: Iff an unknown resolver was defined by another test it will appear here
        """
        resolver_names = {resolver.name for resolver in resolvers()}
        self.assertSetEqual(resolver_names, {'cargo', 'ubuntu', 'autotools', 'go', 'cmake', 'npm', 'pip'})
        self.assertSetEqual(resolvers(), {resolver_by_name(name) for name in resolver_names})

    def test_objects(self):
        # To/From string for nicer output and ergonomics
        self.assertEqual(str(Dependency.from_string("pip:cvedb@*")), "pip:cvedb@*")
        self.assertEqual(str(Package.from_string("pip:cvedb@0.0.1")), "pip:cvedb@0.0.1")

        # Basic Dependency object handling
        dep = Dependency.from_string("pip:cvedb@*")
        self.assertEqual(dep.source, "pip")
        self.assertEqual(dep.package, "cvedb")
        self.assertTrue(dep.semantic_version == SimpleSpec("*"))
        self.assertTrue(Dependency(source="pip", package="cvedb", semantic_version=SimpleSpec("*")) ==
                                    dep)

        # Dependency match
        solution = Package(source="pip", name="cvedb", version="0.0.1")
        self.assertTrue(dep.match(solution))
        dep2 = Dependency.from_string("pip:cvedb@<0.2.1")
        self.assertTrue(dep2.match(Package.from_string("pip:cvedb@0.2.0")))
        self.assertFalse(dep2.match(Package.from_string("pip:cvedb@0.2.1")))

    def _test_resolver(self, resolver, dep):
        dep = Dependency.from_string(dep)
        resolver = resolver_by_name(resolver)
        self.assertIs(dep.resolver, resolver)

        solutions = tuple(resolver.resolve(dep))
        self.assertGreater(len(solutions), 0)
        for package in solutions:
            self.assertEqual(package.source, dep.source)
            self.assertEqual(package.name, dep.package)
            self.assertTrue(dep.semantic_version.match(package.version))
            self.assertTrue(dep.match(package))
        return solutions

    def test_determinism(self):
        """Test if a resolver gives the same solution multiple times in a row.

        Half of the attempts will be without a cache, and the second half will use the same cache.

        """
        cache = InMemoryPackageCache()
        to_test: List[Tuple[Union[Dependency, SourceRepository], int]] = [
            (Dependency.from_string(dep_name), 5) for dep_name in (
                "pip:cvedb@*", "ubuntu:libc6@*", "cargo:rand_core@0.6.2", "npm:crypto-js@4.0.0"
            )
        ]
        to_test.extend([
            (smoke_test.source_repo, 3) for smoke_test in SMOKE_TESTS if smoke_test.repo_name in (
                "bitcoin",
                "pe-parse"
            )
        ])
        for dep, num_attempts in to_test:
            with self.subTest(msg=f"Testing the determinism of dep", dep=dep):
                first_result: Set[Package] = set()
                for i in range(num_attempts):
                    if i < num_attempts // 2:
                        attempt_cache: Optional[InMemoryPackageCache] = None
                    else:
                        attempt_cache = cache
                    result = set(resolve(dep, cache=attempt_cache))
                    if i == 0:
                        first_result = result
                    else:
                        self.assertEqual(first_result, result,
                                         msg=f"Results differed on attempt {i + 1} at resolving {dep}")

    def test_pip(self):
        self._test_resolver("pip", "pip:cvedb@*")

    def test_ubuntu(self):
        self._test_resolver("ubuntu", "ubuntu:libc6@*")

    def test_cargo(self):
        self._test_resolver("cargo", "cargo:rand_core@0.6.2")

    def test_npm(self):
        self._test_resolver("npm", "npm:crypto-js@4.0.0")


class SmokeTest:
    def __init__(self, user_name: str, repo_name: str, commit: str):
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
            urllib.request.urlretrieve(self.url, self._snapshot_zip)
            with zipfile.ZipFile(self._snapshot_zip, "r") as zip_ref:
                zip_ref.extractall(REPOS_FOLDER)
        return self._snapshot_folder

    @property
    def source_repo(self) -> SourceRepository:
        return SourceRepository(self.snapshot_folder)

    def run(self) -> PackageRepository:
        return resolve(self.source_repo)

    def __hash__(self):
        return hash((self.user_name, self.repo_name, self.commit))

    def __eq__(self, other):
        return (
            isinstance(other, SmokeTest) and self.user_name == other.user_name and self.repo_name == other.repo_name
            and self.commit == other.commit
        )


SMOKE_TESTS: Set[SmokeTest] = set()


def gh_smoke_test(user_name: str, repo_name: str, commit: str):
    smoke_test = SmokeTest(user_name, repo_name, commit)
    SMOKE_TESTS.add(smoke_test)

    def do_smoke_test(func):
        @wraps(func)
        def wrapper(self: TestCase):
            package_list = smoke_test.run()
            result_it_depends = package_list.to_obj()
            with open(smoke_test.actual_json, "w") as f:
                f.write(json.dumps(result_it_depends, indent=4, sort_keys=True))

            if not smoke_test.expected_json.exists():
                raise ValueError(f"File {smoke_test.expected_json.absolute()} needs to be created! See "
                                 f"{smoke_test.actual_json.absolute()} for the output of the most recent run.")
            with open(smoke_test.expected_json, "r") as f:
                expected = json.load(f)
            if result_it_depends != expected:
                print(f"See {smoke_test.actual_json.absolute()} for the result of this run.")
            self.assertEqual(result_it_depends, expected)

            return func(self, package_list)

        return wrapper

    return do_smoke_test


class TestSmoke(TestCase):
    maxDiff = None

    def setUp(self) -> None:
        if not os.path.exists(REPOS_FOLDER):
            os.makedirs(REPOS_FOLDER)

    @gh_smoke_test("trailofbits", "cvedb", "7441dc0e238e31829891f85fd840d9e65cb629d8")
    def __test_pip(self, package_list):
        pass

    @gh_smoke_test("trailofbits", "siderophile", "7bca0f5a73da98550c29032f6a2a170f472ea241")
    def __test_cargo(self, package_list):
        pass

    @gh_smoke_test("bitcoin", "bitcoin", "4a267057617a8aa6dc9793c4d711725df5338025")
    def __test_autotools(self, package_list):
        pass

    @gh_smoke_test("brix", "crypto-js", "971c31f0c931f913d22a76ed488d9216ac04e306")
    def __test_npm(self, package_list):
        pass

    # @gh_smoke_test("lifting-bits", "rellic", "9cf73b288a3d0c51d5de7e1060cba8656538596f")
    @gh_smoke_test("trailofbits", "pe-parse", "94bd12ac539382c303896f175a1ab16352e65a8f")
    def __test_cmake(self, package_list):
        pass

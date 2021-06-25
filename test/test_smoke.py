from unittest import TestCase
from pathlib import Path
import os
import json
import urllib
import zipfile
from it_depends.dependencies import SimpleSpec, Package, Dependency, resolve, SourceRepository, resolvers, resolver_by_name

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
        self.assertSetEqual(resolver_names, {'cargo', 'ubuntu', 'native', 'autotools', 'go', 'cmake', 'npm', 'pip'})
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

    def test_pip(self):
        self._test_resolver("pip", "pip:cvedb@*")

    def test_ubuntu(self):
        self._test_resolver("ubuntu", "ubuntu:libc6@*")

    def test_cargo(self):
        self._test_resolver("cargo", "cargo:rand_core@0.6.2")

    def test_npm(self):
        self._test_resolver("npm", "npm:crypto-js@4.0.0")


class TestSmoke(TestCase):
    maxDiff = None

    def setUp(self) -> None:
        if not os.path.exists(REPOS_FOLDER):
            os.makedirs(REPOS_FOLDER)

    def _gh_smoke_test(self, user_name, repo_name, commit, result_json):
        URL = f"https://github.com/{user_name}/{repo_name}/archive/{commit}.zip"
        SNAPSHOT_FOLDER = REPOS_FOLDER / (repo_name + "-" + commit)
        SNAPSHOT_ZIP = SNAPSHOT_FOLDER.with_suffix(".zip")

        if not (SNAPSHOT_FOLDER).exists():
            urllib.request.urlretrieve(URL, SNAPSHOT_ZIP)
            with zipfile.ZipFile(SNAPSHOT_ZIP, "r") as zip_ref:
                zip_ref.extractall(REPOS_FOLDER)

        package_list = resolve(SourceRepository(SNAPSHOT_FOLDER))
        result_it_depends = json.dumps(package_list.to_obj(), indent=4, sort_keys=True)
        if not result_json or result_it_depends != result_json:
            print(f"<{result_it_depends}>")
        self.assertEqual(result_it_depends, result_json)

    def test_pip(self):
        result_json = """{
    "cvedb": {
        "0.0.4": {
            "dependencies": {
                "cvss": "~=2.2",
                "python-dateutil": "~=2.8.1",
                "tqdm": "~=4.48.0",
                "ubuntu:libc6": "*",
                "ubuntu:libtinfo6": "*"
            },
            "is_source_package": true,
            "source": "pip"
        }
    },
    "cvss": {
        "2.2.0": {
            "dependencies": {},
            "source": "pip"
        }
    },
    "gcc-10-base": {
        "10.3.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "libc6": {
        "2.31.0": {
            "dependencies": {
                "libcrypt1": "*",
                "libgcc-s1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libcrypt1": {
        "4.4.10": {
            "dependencies": {
                "libc6": ">=2.25"
            },
            "source": "ubuntu"
        }
    },
    "libgcc-s1": {
        "10.3.0": {
            "dependencies": {
                "gcc-10-base": "*",
                "libc6": ">=2.14"
            },
            "source": "ubuntu"
        }
    },
    "libtinfo6": {
        "6.2.0": {
            "dependencies": {
                "libc6": ">=2.16"
            },
            "source": "ubuntu"
        }
    },
    "python-dateutil": {
        "2.8.1": {
            "dependencies": {
                "six": ">=1.5"
            },
            "source": "pip"
        }
    },
    "six": {
        "1.5.0": {
            "dependencies": {},
            "source": "pip"
        }
    },
    "tqdm": {
        "4.48.0": {
            "dependencies": {},
            "source": "pip"
        }
    }
}"""
        self._gh_smoke_test("trailofbits", "cvedb", "7441dc0e238e31829891f85fd840d9e65cb629d8", result_json)

    def test_cargo(self):
        result_json = """{
    "adler": {
        "0.2.3": {
            "dependencies": {
                "compiler_builtins": "^0.1.2",
                "rustc-std-workspace-core": "^1.0.0"
            },
            "source": "cargo"
        }
    },
    "aho-corasick": {
        "0.7.15": {
            "dependencies": {
                "memchr": "^2.2.0"
            },
            "source": "cargo"
        },
        "0.7.18": {
            "dependencies": {
                "memchr": "^2.4.0"
            },
            "source": "cargo"
        }
    },
    "ansi_term": {
        "0.11.0": {
            "dependencies": {
                "winapi": "^0.3.4"
            },
            "source": "cargo"
        }
    },
    "anyhow": {
        "1.0.38": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "atty": {
        "0.2.14": {
            "dependencies": {
                "hermit-abi": "^0.1.6",
                "libc": "^0.2",
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "bitflags": {
        "1.2.1": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "bitmaps": {
        "2.1.0": {
            "dependencies": {
                "typenum": "^1.10.0"
            },
            "source": "cargo"
        }
    },
    "bstr": {
        "0.2.15": {
            "dependencies": {
                "lazy_static": "^1.2",
                "memchr": "^2.1.2",
                "regex-automata": "^0.1.5",
                "serde": "^1.0.85"
            },
            "source": "cargo"
        }
    },
    "bytesize": {
        "1.0.1": {
            "dependencies": {
                "serde": "^1.0"
            },
            "source": "cargo"
        }
    },
    "cargo": {
        "0.50.1": {
            "dependencies": {
                "anyhow": "^1.0",
                "atty": "^0.2",
                "bytesize": "^1.0",
                "cargo-platform": "^0.1.1",
                "clap": "^2.31.2",
                "core-foundation": "^0.9.0",
                "crates-io": "^0.31.1",
                "crossbeam-utils": "^0.8",
                "crypto-hash": "^0.3.1",
                "curl": "^0.4.23",
                "curl-sys": "^0.4.22",
                "env_logger": "^0.8.1",
                "filetime": "^0.2.9",
                "flate2": "^1.0.3",
                "fwdansi": "^1.1.0",
                "git2": "^0.13.12",
                "git2-curl": "^0.14.0",
                "glob": "^0.3.0",
                "hex": "^0.4",
                "home": "^0.5",
                "humantime": "^2.0.0",
                "ignore": "^0.4.7",
                "im-rc": "^15.0.0",
                "jobserver": "^0.1.21",
                "lazy_static": "^1.2.0",
                "lazycell": "^1.2.0",
                "libc": "^0.2",
                "libgit2-sys": "^0.12.14",
                "log": "^0.4.6",
                "memchr": "^2.1.3",
                "miow": "^0.3.1",
                "num_cpus": "^1.0",
                "opener": "^0.4",
                "openssl": "^0.10.11",
                "percent-encoding": "^2.0",
                "pretty_env_logger": "^0.4",
                "rustc-workspace-hack": "^1.0.0",
                "rustfix": "^0.5.0",
                "same-file": "^1",
                "semver": "^0.10",
                "serde": "^1.0.82",
                "serde_ignored": "^0.1.0",
                "serde_json": "^1.0.30",
                "shell-escape": "^0.1.4",
                "strip-ansi-escapes": "^0.1.0",
                "tar": "^0.4.26",
                "tempfile": "^3.0",
                "termcolor": "^1.1",
                "toml": "^0.5.7",
                "unicode-width": "^0.1.5",
                "unicode-xid": "^0.2.0",
                "url": "^2.0",
                "walkdir": "^2.2",
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "cargo-platform": {
        "0.1.1": {
            "dependencies": {
                "serde": "^1.0.82"
            },
            "source": "cargo"
        }
    },
    "cfg-if": {
        "1.0.0": {
            "dependencies": {
                "compiler_builtins": "^0.1.2",
                "rustc-std-workspace-core": "^1.0.0"
            },
            "source": "cargo"
        }
    },
    "clap": {
        "2.33.3": {
            "dependencies": {
                "ansi_term": "^0.11",
                "atty": "^0.2.2",
                "bitflags": "^1.0",
                "clippy": "~0.0.166",
                "strsim": "^0.8",
                "term_size": "^0.3.0",
                "textwrap": "^0.11.0",
                "unicode-width": "^0.1.4",
                "vec_map": "^0.8",
                "yaml-rust": "^0.3.5"
            },
            "source": "cargo"
        }
    },
    "commoncrypto": {
        "0.2.0": {
            "dependencies": {
                "clippy": "^0.0",
                "commoncrypto-sys": "^0.2.0"
            },
            "source": "cargo"
        }
    },
    "commoncrypto-sys": {
        "0.2.0": {
            "dependencies": {
                "clippy": "^0.0",
                "libc": "^0.2"
            },
            "source": "cargo"
        }
    },
    "core-foundation": {
        "0.9.1": {
            "dependencies": {
                "chrono": "^0.4",
                "core-foundation-sys": "^0.8.0",
                "libc": "^0.2",
                "uuid": "^0.5"
            },
            "source": "cargo"
        }
    },
    "core-foundation-sys": {
        "0.8.2": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "crates-io": {
        "0.31.1": {
            "dependencies": {
                "anyhow": "^1.0.34",
                "curl": "^0.4",
                "percent-encoding": "^2.0",
                "serde": "^1.0",
                "serde_json": "^1.0",
                "url": "^2.0"
            },
            "source": "cargo"
        }
    },
    "crc32fast": {
        "1.2.1": {
            "dependencies": {
                "cfg-if": "^1.0"
            },
            "source": "cargo"
        }
    },
    "crossbeam-utils": {
        "0.8.2": {
            "dependencies": {
                "cfg-if": "^1",
                "lazy_static": "^1.4.0",
                "loom": "^0.4"
            },
            "source": "cargo"
        }
    },
    "crypto-hash": {
        "0.3.4": {
            "dependencies": {
                "commoncrypto": "^0.2",
                "hex": "^0.3",
                "openssl": "^0.10",
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "curl": {
        "0.4.34": {
            "dependencies": {
                "curl-sys": "^0.4.37",
                "libc": "^0.2.42",
                "openssl-probe": "^0.1.2",
                "openssl-sys": "^0.9.43",
                "schannel": "^0.1.13",
                "socket2": "^0.3.7",
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "curl-sys": {
        "0.4.40+curl-7.75.0": {
            "dependencies": {
                "libc": "^0.2.2",
                "libnghttp2-sys": "^0.1.3",
                "libz-sys": "^1.0.18",
                "mesalink": "^1.1.0-cratesio",
                "openssl-sys": "^0.9",
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "either": {
        "1.6.1": {
            "dependencies": {
                "serde": "^1.0"
            },
            "source": "cargo"
        }
    },
    "env_logger": {
        "0.8.3": {
            "dependencies": {
                "atty": "^0.2.5",
                "humantime": "^2.0.0",
                "log": "^0.4.8",
                "regex": "^1.0.3",
                "termcolor": "^1.0.2"
            },
            "source": "cargo"
        },
        "0.8.4": {
            "dependencies": {
                "atty": "^0.2.5",
                "humantime": "^2.0.0",
                "log": "^0.4.8",
                "regex": "^1.0.3",
                "termcolor": "^1.0.2"
            },
            "source": "cargo"
        }
    },
    "filetime": {
        "0.2.14": {
            "dependencies": {
                "cfg-if": "^1.0.0",
                "libc": "^0.2.27",
                "redox_syscall": "^0.2",
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "flate2": {
        "1.0.20": {
            "dependencies": {
                "cfg-if": "^1.0.0",
                "cloudflare-zlib-sys": "^0.2.0",
                "crc32fast": "^1.2.0",
                "futures": "^0.1.25",
                "libc": "^0.2.65",
                "libz-sys": "^1.1.0",
                "miniz-sys": "^0.1.11",
                "miniz_oxide": "^0.4.0,^0.4.0",
                "tokio-io": "^0.1.11"
            },
            "source": "cargo"
        }
    },
    "fnv": {
        "1.0.7": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "foreign-types": {
        "0.3.2": {
            "dependencies": {
                "foreign-types-shared": "^0.1"
            },
            "source": "cargo"
        }
    },
    "foreign-types-shared": {
        "0.1.1": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "form_urlencoded": {
        "1.0.1": {
            "dependencies": {
                "matches": "^0.1",
                "percent-encoding": "^2.1.0"
            },
            "source": "cargo"
        }
    },
    "fwdansi": {
        "1.1.0": {
            "dependencies": {
                "memchr": "^2",
                "termcolor": "^1"
            },
            "source": "cargo"
        }
    },
    "generator": {
        "0.6.24": {
            "dependencies": {
                "libc": "^0.2",
                "log": "^0.4",
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "getrandom": {
        "0.2.2": {
            "dependencies": {
                "cfg-if": "^1",
                "compiler_builtins": "^0.1",
                "js-sys": "^0.3",
                "libc": "^0.2.64",
                "rustc-std-workspace-core": "^1.0",
                "wasi": "^0.10",
                "wasm-bindgen": "^0.2.62"
            },
            "source": "cargo"
        }
    },
    "git2": {
        "0.13.17": {
            "dependencies": {
                "bitflags": "^1.1.0",
                "libc": "^0.2",
                "libgit2-sys": "^0.12.18",
                "log": "^0.4.8",
                "openssl-probe": "^0.1",
                "openssl-sys": "^0.9.0",
                "url": "^2.0"
            },
            "source": "cargo"
        }
    },
    "git2-curl": {
        "0.14.1": {
            "dependencies": {
                "curl": "^0.4.33",
                "git2": "^0.13",
                "log": "^0.4",
                "url": "^2.0"
            },
            "source": "cargo"
        }
    },
    "glob": {
        "0.3.0": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "globset": {
        "0.4.6": {
            "dependencies": {
                "aho-corasick": "^0.7.3",
                "bstr": "^0.2.0",
                "fnv": "^1.0.6",
                "log": "^0.4.5",
                "regex": "^1.1.5",
                "serde": "^1.0.104"
            },
            "source": "cargo"
        }
    },
    "heck": {
        "0.3.2": {
            "dependencies": {
                "unicode-segmentation": "^1.2.0"
            },
            "source": "cargo"
        }
    },
    "hermit-abi": {
        "0.1.18": {
            "dependencies": {
                "compiler_builtins": "^0.1.0",
                "libc": "^0.2.51",
                "rustc-std-workspace-core": "^1.0.0"
            },
            "source": "cargo"
        }
    },
    "hex": {
        "0.3.2": {
            "dependencies": {},
            "source": "cargo"
        },
        "0.4.2": {
            "dependencies": {
                "serde": "^1.0"
            },
            "source": "cargo"
        }
    },
    "home": {
        "0.5.3": {
            "dependencies": {
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "humantime": {
        "2.1.0": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "idna": {
        "0.2.2": {
            "dependencies": {
                "matches": "^0.1",
                "unicode-bidi": "^0.3",
                "unicode-normalization": "^0.1.17"
            },
            "source": "cargo"
        }
    },
    "ignore": {
        "0.4.17": {
            "dependencies": {
                "crossbeam-utils": "^0.8.0",
                "globset": "^0.4.5",
                "lazy_static": "^1.1",
                "log": "^0.4.5",
                "memchr": "^2.1",
                "regex": "^1.1",
                "same-file": "^1.0.4",
                "thread_local": "^1",
                "walkdir": "^2.2.7",
                "winapi-util": "^0.1.2"
            },
            "source": "cargo"
        }
    },
    "im-rc": {
        "15.0.0": {
            "dependencies": {
                "arbitrary": "^0.4",
                "bitmaps": "^2",
                "proptest": "^0.9",
                "quickcheck": "^0.9",
                "rand_core": "^0.5.1",
                "rand_xoshiro": "^0.4",
                "rayon": "^1",
                "refpool": "^0.4",
                "serde": "^1",
                "sized-chunks": "^0.6",
                "typenum": "^1.12"
            },
            "source": "cargo"
        }
    },
    "itoa": {
        "0.4.7": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "jobserver": {
        "0.1.21": {
            "dependencies": {
                "libc": "^0.2.50"
            },
            "source": "cargo"
        }
    },
    "lazy_static": {
        "1.4.0": {
            "dependencies": {
                "spin": "^0.5.0"
            },
            "source": "cargo"
        }
    },
    "lazycell": {
        "1.3.0": {
            "dependencies": {
                "clippy": "^0.0",
                "serde": "^1"
            },
            "source": "cargo"
        }
    },
    "libc": {
        "0.2.86": {
            "dependencies": {
                "rustc-std-workspace-core": "^1.0.0"
            },
            "source": "cargo"
        }
    },
    "libgit2-sys": {
        "0.12.18+1.1.0": {
            "dependencies": {
                "libc": "^0.2",
                "libssh2-sys": "^0.2.19",
                "libz-sys": "^1.1.0",
                "openssl-sys": "^0.9"
            },
            "source": "cargo"
        }
    },
    "libnghttp2-sys": {
        "0.1.6+1.43.0": {
            "dependencies": {
                "libc": "^0.2"
            },
            "source": "cargo"
        }
    },
    "libssh2-sys": {
        "0.2.21": {
            "dependencies": {
                "libc": "^0.2",
                "libz-sys": "^1.1.0",
                "openssl-sys": "^0.9.35"
            },
            "source": "cargo"
        }
    },
    "libz-sys": {
        "1.1.2": {
            "dependencies": {
                "libc": "^0.2.43"
            },
            "source": "cargo"
        }
    },
    "llvm-ir": {
        "0.7.5": {
            "dependencies": {
                "either": "^1.6",
                "llvm-sys": "^100.2.0,^110.0.0,^120.0.0,^80.3.0,^90.2.0",
                "log": "^0.4.0"
            },
            "source": "cargo"
        }
    },
    "log": {
        "0.4.14": {
            "dependencies": {
                "cfg-if": "^1.0",
                "serde": "^1.0",
                "sval": "^1.0.0-alpha.5",
                "value-bag": "^1.0.0-alpha.6"
            },
            "source": "cargo"
        }
    },
    "loom": {
        "0.4.0": {
            "dependencies": {
                "cfg-if": "^1.0.0",
                "futures-util": "^0.3.0",
                "generator": "^0.6.18",
                "scoped-tls": "^1.0.0",
                "serde": "^1.0.92",
                "serde_json": "^1.0.33"
            },
            "source": "cargo"
        }
    },
    "matches": {
        "0.1.8": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "memchr": {
        "2.3.4": {
            "dependencies": {
                "libc": "^0.2.18"
            },
            "source": "cargo"
        },
        "2.4.0": {
            "dependencies": {
                "libc": "^0.2.18"
            },
            "source": "cargo"
        }
    },
    "miniz_oxide": {
        "0.4.3": {
            "dependencies": {
                "adler": "^0.2.3",
                "compiler_builtins": "^0.1.2",
                "rustc-std-workspace-alloc": "^1.0.0",
                "rustc-std-workspace-core": "^1.0.0"
            },
            "source": "cargo"
        }
    },
    "miow": {
        "0.3.6": {
            "dependencies": {
                "socket2": "^0.3.16",
                "winapi": "^0.3.3"
            },
            "source": "cargo"
        }
    },
    "num_cpus": {
        "1.13.0": {
            "dependencies": {
                "hermit-abi": "^0.1.3",
                "libc": "^0.2.26"
            },
            "source": "cargo"
        }
    },
    "once_cell": {
        "1.6.0": {
            "dependencies": {
                "parking_lot": "^0.11"
            },
            "source": "cargo"
        }
    },
    "opener": {
        "0.4.1": {
            "dependencies": {
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "openssl": {
        "0.10.32": {
            "dependencies": {
                "bitflags": "^1.0",
                "cfg-if": "^1.0",
                "foreign-types": "^0.3.1",
                "lazy_static": "^1",
                "libc": "^0.2",
                "openssl-sys": "^0.9.60"
            },
            "source": "cargo"
        }
    },
    "openssl-probe": {
        "0.1.2": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "openssl-sys": {
        "0.9.60": {
            "dependencies": {
                "libc": "^0.2"
            },
            "source": "cargo"
        }
    },
    "percent-encoding": {
        "2.1.0": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "ppv-lite86": {
        "0.2.10": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "proc-macro-error": {
        "1.0.4": {
            "dependencies": {
                "proc-macro-error-attr": "=1.0.4",
                "proc-macro2": "^1",
                "quote": "^1",
                "syn": "^1"
            },
            "source": "cargo"
        }
    },
    "proc-macro-error-attr": {
        "1.0.4": {
            "dependencies": {
                "proc-macro2": "^1",
                "quote": "^1"
            },
            "source": "cargo"
        }
    },
    "proc-macro2": {
        "1.0.24": {
            "dependencies": {
                "unicode-xid": "^0.2"
            },
            "source": "cargo"
        }
    },
    "quote": {
        "1.0.9": {
            "dependencies": {
                "proc-macro2": "^1.0.20"
            },
            "source": "cargo"
        }
    },
    "rand": {
        "0.8.3": {
            "dependencies": {
                "libc": "^0.2.22",
                "log": "^0.4.4",
                "packed_simd_2": "^0.3.4",
                "rand_chacha": "^0.3.0",
                "rand_core": "^0.6.0",
                "rand_hc": "^0.3.0",
                "serde": "^1.0.103"
            },
            "source": "cargo"
        }
    },
    "rand_chacha": {
        "0.3.0": {
            "dependencies": {
                "ppv-lite86": "^0.2.8",
                "rand_core": "^0.6.0"
            },
            "source": "cargo"
        }
    },
    "rand_core": {
        "0.5.1": {
            "dependencies": {
                "getrandom": "^0.1",
                "serde": "^1"
            },
            "source": "cargo"
        },
        "0.6.2": {
            "dependencies": {
                "getrandom": "^0.2",
                "serde": "^1"
            },
            "source": "cargo"
        }
    },
    "rand_hc": {
        "0.3.0": {
            "dependencies": {
                "rand_core": "^0.6.0"
            },
            "source": "cargo"
        }
    },
    "rand_xoshiro": {
        "0.4.0": {
            "dependencies": {
                "rand_core": "^0.5",
                "serde": "^1"
            },
            "source": "cargo"
        }
    },
    "redox_syscall": {
        "0.2.5": {
            "dependencies": {
                "bitflags": "^1.1.0"
            },
            "source": "cargo"
        }
    },
    "regex": {
        "1.4.3": {
            "dependencies": {
                "aho-corasick": "^0.7.6",
                "memchr": "^2.2.1",
                "regex-syntax": "^0.6.22",
                "thread_local": "^1"
            },
            "source": "cargo"
        },
        "1.5.4": {
            "dependencies": {
                "aho-corasick": "^0.7.18",
                "memchr": "^2.4.0",
                "regex-syntax": "^0.6.25"
            },
            "source": "cargo"
        }
    },
    "regex-syntax": {
        "0.6.22": {
            "dependencies": {},
            "source": "cargo"
        },
        "0.6.25": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "remove_dir_all": {
        "0.5.3": {
            "dependencies": {
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "rustc-demangle": {
        "0.1.20": {
            "dependencies": {
                "compiler_builtins": "^0.1.2",
                "rustc-std-workspace-core": "^1.0.0"
            },
            "source": "cargo"
        }
    },
    "rustc-workspace-hack": {
        "1.0.0": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "rustfix": {
        "0.5.1": {
            "dependencies": {
                "anyhow": "^1.0.0",
                "log": "^0.4.1",
                "serde": "^1.0",
                "serde_json": "^1.0"
            },
            "source": "cargo"
        }
    },
    "ryu": {
        "1.0.5": {
            "dependencies": {
                "no-panic": "^0.1"
            },
            "source": "cargo"
        }
    },
    "same-file": {
        "1.0.6": {
            "dependencies": {
                "winapi-util": "^0.1.1"
            },
            "source": "cargo"
        }
    },
    "schannel": {
        "0.1.19": {
            "dependencies": {
                "lazy_static": "^1.0",
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "scoped-tls": {
        "1.0.0": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "semver": {
        "0.10.0": {
            "dependencies": {
                "diesel": "^1.1",
                "semver-parser": "^0.7.0",
                "serde": "^1.0"
            },
            "source": "cargo"
        }
    },
    "semver-parser": {
        "0.7.0": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "serde": {
        "1.0.123": {
            "dependencies": {
                "serde_derive": "=1.0.123"
            },
            "source": "cargo"
        }
    },
    "serde_derive": {
        "1.0.123": {
            "dependencies": {
                "proc-macro2": "^1.0",
                "quote": "^1.0",
                "syn": "^1.0.60"
            },
            "source": "cargo"
        }
    },
    "serde_ignored": {
        "0.1.2": {
            "dependencies": {
                "serde": "^1.0"
            },
            "source": "cargo"
        }
    },
    "serde_json": {
        "1.0.62": {
            "dependencies": {
                "indexmap": "^1.5",
                "itoa": "^0.4.3",
                "ryu": "^1.0",
                "serde": "^1.0.100"
            },
            "source": "cargo"
        }
    },
    "shell-escape": {
        "0.1.5": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "siderophile": {
        "0.1.1": {
            "dependencies": {
                "anyhow": "^1",
                "cargo": "^0.50.1",
                "env_logger": "^0.8",
                "glob": "^0.3",
                "llvm-ir": "^0.7.4",
                "log": "^0.4",
                "quote": "^1.0.9",
                "regex": "^1",
                "rustc-demangle": "^0.1",
                "structopt": "^0.3",
                "syn": "^1.0",
                "tempfile": "^3.1.0",
                "walkdir": "^2.3"
            },
            "is_source_package": true,
            "source": "cargo"
        }
    },
    "sized-chunks": {
        "0.6.4": {
            "dependencies": {
                "arbitrary": "^0.4.7",
                "array-ops": "^0.1.0",
                "bitmaps": "^2.1.0",
                "refpool": "^0.4.3",
                "typenum": "^1.12.0"
            },
            "source": "cargo"
        }
    },
    "socket2": {
        "0.3.19": {
            "dependencies": {
                "cfg-if": "^1.0",
                "libc": "^0.2.66",
                "winapi": "^0.3.3"
            },
            "source": "cargo"
        }
    },
    "strip-ansi-escapes": {
        "0.1.0": {
            "dependencies": {
                "vte": "^0.3.2"
            },
            "source": "cargo"
        }
    },
    "strsim": {
        "0.8.0": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "structopt": {
        "0.3.21": {
            "dependencies": {
                "clap": "^2.33",
                "lazy_static": "^1.4.0",
                "paw": "^1",
                "structopt-derive": "=0.4.14"
            },
            "source": "cargo"
        }
    },
    "structopt-derive": {
        "0.4.14": {
            "dependencies": {
                "heck": "^0.3.0",
                "proc-macro-error": "^1.0.0",
                "proc-macro2": "^1",
                "quote": "^1",
                "syn": "^1"
            },
            "source": "cargo"
        }
    },
    "syn": {
        "1.0.60": {
            "dependencies": {
                "proc-macro2": "^1.0.23",
                "quote": "^1.0",
                "unicode-xid": "^0.2"
            },
            "source": "cargo"
        }
    },
    "tar": {
        "0.4.33": {
            "dependencies": {
                "filetime": "^0.2.8",
                "libc": "^0.2",
                "xattr": "^0.2"
            },
            "source": "cargo"
        }
    },
    "tempfile": {
        "3.2.0": {
            "dependencies": {
                "cfg-if": "^1",
                "libc": "^0.2.27",
                "rand": "^0.8",
                "redox_syscall": "^0.2",
                "remove_dir_all": "^0.5",
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "termcolor": {
        "1.1.2": {
            "dependencies": {
                "winapi-util": "^0.1.3"
            },
            "source": "cargo"
        }
    },
    "textwrap": {
        "0.11.0": {
            "dependencies": {
                "hyphenation": "^0.7.1",
                "term_size": "^0.3.0",
                "unicode-width": "^0.1.3"
            },
            "source": "cargo"
        }
    },
    "thread_local": {
        "1.1.3": {
            "dependencies": {
                "criterion": "^0.3.3",
                "once_cell": "^1.5.2"
            },
            "source": "cargo"
        }
    },
    "tinyvec": {
        "1.1.1": {
            "dependencies": {
                "serde": "^1.0",
                "tinyvec_macros": "^0.1"
            },
            "source": "cargo"
        }
    },
    "tinyvec_macros": {
        "0.1.0": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "toml": {
        "0.5.8": {
            "dependencies": {
                "indexmap": "^1.0",
                "serde": "^1.0.97"
            },
            "source": "cargo"
        }
    },
    "typenum": {
        "1.12.0": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "unicode-bidi": {
        "0.3.4": {
            "dependencies": {
                "flame": "^0.1",
                "flamer": "^0.1",
                "matches": "^0.1",
                "serde": ">=0.8,<2.0"
            },
            "source": "cargo"
        }
    },
    "unicode-normalization": {
        "0.1.17": {
            "dependencies": {
                "tinyvec": "^1"
            },
            "source": "cargo"
        }
    },
    "unicode-segmentation": {
        "1.7.1": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "unicode-width": {
        "0.1.8": {
            "dependencies": {
                "compiler_builtins": "^0.1",
                "rustc-std-workspace-core": "^1.0",
                "rustc-std-workspace-std": "^1.0"
            },
            "source": "cargo"
        }
    },
    "unicode-xid": {
        "0.2.1": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "url": {
        "2.2.1": {
            "dependencies": {
                "form_urlencoded": "^1.0.0",
                "idna": "^0.2.0",
                "matches": "^0.1",
                "percent-encoding": "^2.1.0",
                "serde": "^1.0"
            },
            "source": "cargo"
        }
    },
    "utf8parse": {
        "0.1.1": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "vec_map": {
        "0.8.2": {
            "dependencies": {
                "serde": "^1.0"
            },
            "source": "cargo"
        }
    },
    "vte": {
        "0.3.3": {
            "dependencies": {
                "utf8parse": "^0.1"
            },
            "source": "cargo"
        }
    },
    "walkdir": {
        "2.3.1": {
            "dependencies": {
                "same-file": "^1.0.1",
                "winapi": "^0.3",
                "winapi-util": "^0.1.1"
            },
            "source": "cargo"
        },
        "2.3.2": {
            "dependencies": {
                "same-file": "^1.0.1",
                "winapi": "^0.3",
                "winapi-util": "^0.1.1"
            },
            "source": "cargo"
        }
    },
    "wasi": {
        "0.10.2+wasi-snapshot-preview1": {
            "dependencies": {
                "compiler_builtins": "^0.1",
                "rustc-std-workspace-alloc": "^1.0",
                "rustc-std-workspace-core": "^1.0"
            },
            "source": "cargo"
        }
    },
    "winapi": {
        "0.3.9": {
            "dependencies": {
                "winapi-i686-pc-windows-gnu": "^0.4",
                "winapi-x86_64-pc-windows-gnu": "^0.4"
            },
            "source": "cargo"
        }
    },
    "winapi-i686-pc-windows-gnu": {
        "0.4.0": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "winapi-util": {
        "0.1.5": {
            "dependencies": {
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "winapi-x86_64-pc-windows-gnu": {
        "0.4.0": {
            "dependencies": {},
            "source": "cargo"
        }
    }
}"""
        self._gh_smoke_test("trailofbits", "siderophile", "7bca0f5a73da98550c29032f6a2a170f472ea241", result_json)

    def test_npm(self):
        result_json = """{
    "crypto-js": {
        "4.0.0": {
            "dependencies": {
                "ubuntu:libc6": "*",
                "ubuntu:libtinfo6": "*"
            },
            "is_source_package": true,
            "source": "npm"
        }
    },
    "gcc-10-base": {
        "10.3.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "libc6": {
        "2.31.0": {
            "dependencies": {
                "libcrypt1": "*",
                "libgcc-s1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libcrypt1": {
        "4.4.10": {
            "dependencies": {
                "libc6": ">=2.25"
            },
            "source": "ubuntu"
        }
    },
    "libgcc-s1": {
        "10.3.0": {
            "dependencies": {
                "gcc-10-base": "*",
                "libc6": ">=2.14"
            },
            "source": "ubuntu"
        }
    },
    "libtinfo6": {
        "6.2.0": {
            "dependencies": {
                "libc6": ">=2.16"
            },
            "source": "ubuntu"
        }
    }
}"""
        self._gh_smoke_test("brix", "crypto-js", "971c31f0c931f913d22a76ed488d9216ac04e306", result_json)

    def __test_autotools(self):
        result_json = """{
    "Bitcoin Core": {
        "21.99.0": {
            "dependencies": {
                "gcc-snapshot": "*",
                "libc6-dev": "*",
                "libevent-dev": ">=2.0.21",
                "libminiupnpc-dev": "*",
                "libnatpmp-dev": "*",
                "libqrencode-dev": "*",
                "libsqlite3-dev": ">=3.7.17",
                "libunivalue-dev": ">=1.0.4",
                "libzfslinux-dev": "*",
                "libzmq3-dev": ">=4",
                "mingw-w64-i686-dev": "*",
                "qtbase5-dev": ">=5.9.5"
            },
            "is_source_package": true,
            "source": "autotools"
        }
    },
    "binfmt-support": {
        "2.2.0": {
            "dependencies": {
                "libc6": "*",
                "libpipeline1": "*",
                "lsb-base": "*"
            },
            "source": "ubuntu"
        }
    },
    "binutils": {
        "2.34.0": {
            "dependencies": {
                "binutils-common": "*",
                "binutils-x86-64-linux-gnu": "*",
                "libbinutils": "*"
            },
            "source": "ubuntu"
        }
    },
    "binutils-common": {
        "2.34.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "binutils-x86-64-linux-gnu": {
        "2.34.0": {
            "dependencies": {
                "binutils-common": "*",
                "libbinutils": "*",
                "libc6": "*",
                "libctf-nobfd0": "*",
                "libctf0": "*",
                "libgcc-s1": "*",
                "libstdc++6": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "comerr-dev": {
        "2.1.0": {
            "dependencies": {
                "libc6-dev": "*",
                "libcom-err2": "*"
            },
            "source": "ubuntu"
        }
    },
    "coreutils": {
        "8.30.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "debconf": {
        "1.5.73": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "dpkg": {
        "1.19.7-ubuntu3": {
            "dependencies": {
                "tar": "*"
            },
            "source": "ubuntu"
        }
    },
    "fontconfig": {
        "2.13.1": {
            "dependencies": {
                "fontconfig-config": "*",
                "libc6": "*",
                "libfontconfig1": "*",
                "libfreetype6": "*"
            },
            "source": "ubuntu"
        }
    },
    "fontconfig-config": {
        "2.13.1": {
            "dependencies": {
                "fonts-dejavu-core": "*",
                "ucf": "*"
            },
            "source": "ubuntu"
        }
    },
    "fonts-dejavu-core": {
        "2.37.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "gcc-10-base": {
        "10.2.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "gcc-snapshot": {
        "20200418.0.0": {
            "dependencies": {
                "binutils": "*",
                "lib32quadmath0": "*",
                "lib32stdc++6": "*",
                "lib32z1": "*",
                "libc6": "*",
                "libc6-dev": "*",
                "libc6-dev-i386": "*",
                "libc6-dev-x32": "*",
                "libc6-i386": "*",
                "libc6-x32": "*",
                "libgc1c2": "*",
                "libgmp10": "*",
                "libisl22": "*",
                "libmpc3": "*",
                "libmpfr6": "*",
                "libquadmath0": "*",
                "libstdc++6": "*",
                "libx32quadmath0": "*",
                "libx32stdc++6": "*",
                "libx32z1": "*",
                "libzstd1": "*",
                "lld-10": "*",
                "llvm-10": "*",
                "python3": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "krb5-multidev": {
        "1.17.0": {
            "dependencies": {
                "comerr-dev": "*",
                "libgssapi-krb5-2": "*",
                "libgssrpc4": "*",
                "libk5crypto3": "*",
                "libkadm5clnt-mit11": "*",
                "libkadm5srv-mit11": "*",
                "libkrb5-3": "*"
            },
            "source": "ubuntu"
        }
    },
    "lib32gcc-s1": {
        "10.2.0": {
            "dependencies": {
                "gcc-10-base": "*",
                "libc6-i386": "*"
            },
            "source": "ubuntu"
        }
    },
    "lib32quadmath0": {
        "10.2.0": {
            "dependencies": {
                "gcc-10-base": "*",
                "libc6-i386": "*"
            },
            "source": "ubuntu"
        }
    },
    "lib32stdc++6": {
        "10.2.0": {
            "dependencies": {
                "gcc-10-base": "*",
                "lib32gcc-s1": "*",
                "libc6-i386": "*"
            },
            "source": "ubuntu"
        }
    },
    "lib32z1": {
        "1.2.11+dfsg": {
            "dependencies": {
                "libc6-i386": "*"
            },
            "source": "ubuntu"
        }
    },
    "libavahi-client3": {
        "0.7.0": {
            "dependencies": {
                "libavahi-common3": "*",
                "libc6": "*",
                "libdbus-1-3": "*"
            },
            "source": "ubuntu"
        }
    },
    "libavahi-common-data": {
        "0.7.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "libavahi-common3": {
        "0.7.0": {
            "dependencies": {
                "libavahi-common-data": "*",
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libbinutils": {
        "2.34.0": {
            "dependencies": {
                "binutils-common": "*",
                "libc6": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libblkid1": {
        "2.34.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libbsd0": {
        "0.10.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libbz2-1.0": {
        "1.0.8": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libc-dev-bin": {
        "2.31.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libc6": {
        "2.31.0": {
            "dependencies": {
                "libcrypt1": "*",
                "libgcc-s1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libc6-dev": {
        "2.31.0": {
            "dependencies": {
                "libc-dev-bin": "*",
                "libc6": "*",
                "libcrypt-dev": "*",
                "linux-libc-dev": "*"
            },
            "source": "ubuntu"
        }
    },
    "libc6-dev-i386": {
        "2.31.0": {
            "dependencies": {
                "libc6-dev": "*",
                "libc6-i386": "*"
            },
            "source": "ubuntu"
        }
    },
    "libc6-dev-x32": {
        "2.31.0": {
            "dependencies": {
                "libc6-dev": "*",
                "libc6-dev-i386": "*",
                "libc6-x32": "*"
            },
            "source": "ubuntu"
        }
    },
    "libc6-i386": {
        "2.31.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libc6-x32": {
        "2.31.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libcom-err2": {
        "1.45.5": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libcrypt-dev": {
        "4.4.10": {
            "dependencies": {
                "libcrypt1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libcrypt1": {
        "4.4.10": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libctf-nobfd0": {
        "2.34.0": {
            "dependencies": {
                "libc6": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libctf0": {
        "2.34.0": {
            "dependencies": {
                "libbinutils": "*",
                "libc6": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libcups2": {
        "2.3.1": {
            "dependencies": {
                "libavahi-client3": "*",
                "libavahi-common3": "*",
                "libc6": "*",
                "libgnutls30": "*",
                "libgssapi-krb5-2": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libdb5.3": {
        "5.3.28+dfsg1": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libdbus-1-3": {
        "1.12.16": {
            "dependencies": {
                "libc6": "*",
                "libsystemd0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libdouble-conversion3": {
        "3.1.5": {
            "dependencies": {
                "libc6": "*",
                "libgcc1": "*",
                "libstdc++6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libdrm-amdgpu1": {
        "2.4.102": {
            "dependencies": {
                "libc6": "*",
                "libdrm2": "*"
            },
            "source": "ubuntu"
        }
    },
    "libdrm-common": {
        "2.4.102": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "libdrm-intel1": {
        "2.4.102": {
            "dependencies": {
                "libc6": "*",
                "libdrm2": "*",
                "libpciaccess0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libdrm-nouveau2": {
        "2.4.102": {
            "dependencies": {
                "libc6": "*",
                "libdrm2": "*"
            },
            "source": "ubuntu"
        }
    },
    "libdrm-radeon1": {
        "2.4.102": {
            "dependencies": {
                "libc6": "*",
                "libdrm2": "*"
            },
            "source": "ubuntu"
        }
    },
    "libdrm2": {
        "2.4.102": {
            "dependencies": {
                "libc6": "*",
                "libdrm-common": "*"
            },
            "source": "ubuntu"
        }
    },
    "libedit2": {
        "3.1.0": {
            "dependencies": {
                "libbsd0": "*",
                "libc6": "*",
                "libtinfo6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libegl-dev": {
        "1.3.2": {
            "dependencies": {
                "libegl1": "*",
                "libgl-dev": "*",
                "libx11-dev": "*"
            },
            "source": "ubuntu"
        }
    },
    "libegl-mesa0": {
        "20.2.6": {
            "dependencies": {
                "libc6": "*",
                "libdrm2": "*",
                "libexpat1": "*",
                "libgbm1": "*",
                "libglapi-mesa": "*",
                "libwayland-client0": "*",
                "libwayland-server0": "*",
                "libx11-xcb1": "*",
                "libxcb-dri2-0": "*",
                "libxcb-dri3-0": "*",
                "libxcb-present0": "*",
                "libxcb-sync1": "*",
                "libxcb-xfixes0": "*",
                "libxcb1": "*",
                "libxshmfence1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libegl1": {
        "1.3.2": {
            "dependencies": {
                "libc6": "*",
                "libegl-mesa0": "*",
                "libglvnd0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libelf1": {
        "0.176.0": {
            "dependencies": {
                "libc6": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libevdev2": {
        "1.9.0+dfsg": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libevent-2.1-7": {
        "2.1.11": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libevent-core-2.1-7": {
        "2.1.11": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libevent-dev": {
        "2.1.11": {
            "dependencies": {
                "libevent-2.1-7": "*",
                "libevent-core-2.1-7": "*",
                "libevent-extra-2.1-7": "*",
                "libevent-openssl-2.1-7": "*",
                "libevent-pthreads-2.1-7": "*"
            },
            "source": "ubuntu"
        }
    },
    "libevent-extra-2.1-7": {
        "2.1.11": {
            "dependencies": {
                "libc6": "*",
                "libevent-core-2.1-7": "*"
            },
            "source": "ubuntu"
        }
    },
    "libevent-openssl-2.1-7": {
        "2.1.11": {
            "dependencies": {
                "libc6": "*",
                "libevent-core-2.1-7": "*",
                "libssl1.1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libevent-pthreads-2.1-7": {
        "2.1.11": {
            "dependencies": {
                "libc6": "*",
                "libevent-core-2.1-7": "*"
            },
            "source": "ubuntu"
        }
    },
    "libexpat1": {
        "2.2.9": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libffi7": {
        "3.3.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libfontconfig1": {
        "2.13.1": {
            "dependencies": {
                "fontconfig-config": "*",
                "libc6": "*",
                "libexpat1": "*",
                "libfreetype6": "*",
                "libuuid1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libfreetype6": {
        "2.10.1": {
            "dependencies": {
                "libc6": "*",
                "libpng16-16": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgbm1": {
        "20.2.6": {
            "dependencies": {
                "libc6": "*",
                "libdrm2": "*",
                "libexpat1": "*",
                "libwayland-server0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgc1c2": {
        "7.6.4": {
            "dependencies": {
                "libc6": "*",
                "libgcc-s1": "*",
                "libstdc++6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgcc-s1": {
        "10.2.0": {
            "dependencies": {
                "gcc-10-base": "*",
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgcc1": {
        "10.2.0": {
            "dependencies": {
                "gcc-10-base": "*",
                "libc6": "*",
                "libgcc-s1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgdbm-compat4": {
        "1.18.1": {
            "dependencies": {
                "libc6": "*",
                "libgdbm6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgdbm6": {
        "1.18.1": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgl-dev": {
        "1.3.2": {
            "dependencies": {
                "libgl1": "*",
                "libglx-dev": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgl1": {
        "1.3.2": {
            "dependencies": {
                "libc6": "*",
                "libglvnd0": "*",
                "libglx0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgl1-mesa-dev": {
        "20.2.6": {
            "dependencies": {
                "libgl-dev": "*",
                "libglvnd-dev": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgl1-mesa-dri": {
        "20.2.6": {
            "dependencies": {
                "libc6": "*",
                "libdrm-amdgpu1": "*",
                "libdrm-intel1": "*",
                "libdrm-nouveau2": "*",
                "libdrm-radeon1": "*",
                "libdrm2": "*",
                "libelf1": "*",
                "libexpat1": "*",
                "libgcc-s1": "*",
                "libglapi-mesa": "*",
                "libllvm11": "*",
                "libsensors5": "*",
                "libstdc++6": "*",
                "libvulkan1": "*",
                "libzstd1": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libglapi-mesa": {
        "20.2.6": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgles-dev": {
        "1.3.2": {
            "dependencies": {
                "libegl-dev": "*",
                "libgl-dev": "*",
                "libgles1": "*",
                "libgles2": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgles1": {
        "1.3.2": {
            "dependencies": {
                "libc6": "*",
                "libglvnd0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgles2": {
        "1.3.2": {
            "dependencies": {
                "libc6": "*",
                "libglvnd0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libglib2.0-0": {
        "2.64.6": {
            "dependencies": {
                "libc6": "*",
                "libffi7": "*",
                "libmount1": "*",
                "libpcre3": "*",
                "libselinux1": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libglu1-mesa": {
        "9.0.1": {
            "dependencies": {
                "libc6": "*",
                "libgcc-s1": "*",
                "libgl1": "*",
                "libstdc++6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libglu1-mesa-dev": {
        "9.0.1": {
            "dependencies": {
                "libgl1-mesa-dev": "*",
                "libglu1-mesa": "*"
            },
            "source": "ubuntu"
        }
    },
    "libglvnd-dev": {
        "1.3.2": {
            "dependencies": {
                "libegl-dev": "*",
                "libgl-dev": "*",
                "libgles-dev": "*",
                "libglvnd0": "*",
                "libglx-dev": "*",
                "libopengl-dev": "*"
            },
            "source": "ubuntu"
        }
    },
    "libglvnd0": {
        "1.3.2": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libglx-dev": {
        "1.3.2": {
            "dependencies": {
                "libglx0": "*",
                "libx11-dev": "*"
            },
            "source": "ubuntu"
        }
    },
    "libglx-mesa0": {
        "20.2.6": {
            "dependencies": {
                "libc6": "*",
                "libdrm2": "*",
                "libexpat1": "*",
                "libgl1-mesa-dri": "*",
                "libglapi-mesa": "*",
                "libx11-6": "*",
                "libx11-xcb1": "*",
                "libxcb-dri2-0": "*",
                "libxcb-dri3-0": "*",
                "libxcb-glx0": "*",
                "libxcb-present0": "*",
                "libxcb-sync1": "*",
                "libxcb-xfixes0": "*",
                "libxcb1": "*",
                "libxdamage1": "*",
                "libxext6": "*",
                "libxfixes3": "*",
                "libxshmfence1": "*",
                "libxxf86vm1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libglx0": {
        "1.3.2": {
            "dependencies": {
                "libc6": "*",
                "libglvnd0": "*",
                "libglx-mesa0": "*",
                "libx11-6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgmp10": {
        "6.2.0+dfsg": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgnutls30": {
        "3.6.13": {
            "dependencies": {
                "libc6": "*",
                "libgmp10": "*",
                "libhogweed5": "*",
                "libidn2-0": "*",
                "libnettle7": "*",
                "libp11-kit0": "*",
                "libtasn1-6": "*",
                "libunistring2": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgraphite2-3": {
        "1.3.13": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgssapi-krb5-2": {
        "1.17.0": {
            "dependencies": {
                "libc6": "*",
                "libcom-err2": "*",
                "libk5crypto3": "*",
                "libkrb5-3": "*",
                "libkrb5support0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgssrpc4": {
        "1.17.0": {
            "dependencies": {
                "libc6": "*",
                "libgssapi-krb5-2": "*"
            },
            "source": "ubuntu"
        }
    },
    "libgudev-1.0-0": {
        "233.0.0": {
            "dependencies": {
                "libc6": "*",
                "libglib2.0-0": "*",
                "libudev1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libharfbuzz0b": {
        "2.6.4": {
            "dependencies": {
                "libc6": "*",
                "libfreetype6": "*",
                "libglib2.0-0": "*",
                "libgraphite2-3": "*"
            },
            "source": "ubuntu"
        }
    },
    "libhogweed5": {
        "3.5.1+really3.5.1": {
            "dependencies": {
                "libc6": "*",
                "libgmp10": "*",
                "libnettle7": "*"
            },
            "source": "ubuntu"
        }
    },
    "libice6": {
        "1.0.10": {
            "dependencies": {
                "libbsd0": "*",
                "libc6": "*",
                "x11-common": "*"
            },
            "source": "ubuntu"
        }
    },
    "libicu66": {
        "66.1.0": {
            "dependencies": {
                "libc6": "*",
                "libgcc-s1": "*",
                "libstdc++6": "*",
                "tzdata": "*"
            },
            "source": "ubuntu"
        }
    },
    "libidn2-0": {
        "2.2.0": {
            "dependencies": {
                "libc6": "*",
                "libunistring2": "*"
            },
            "source": "ubuntu"
        }
    },
    "libinput-bin": {
        "1.15.5": {
            "dependencies": {
                "libc6": "*",
                "libevdev2": "*",
                "libudev1": "*",
                "libwacom2": "*"
            },
            "source": "ubuntu"
        }
    },
    "libinput10": {
        "1.15.5": {
            "dependencies": {
                "libc6": "*",
                "libevdev2": "*",
                "libinput-bin": "*",
                "libmtdev1": "*",
                "libudev1": "*",
                "libwacom2": "*"
            },
            "source": "ubuntu"
        }
    },
    "libisl22": {
        "0.22.1": {
            "dependencies": {
                "libc6": "*",
                "libgmp10": "*"
            },
            "source": "ubuntu"
        }
    },
    "libjpeg-turbo8": {
        "2.0.3": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libjpeg8": {
        "8.0.0-c": {
            "dependencies": {
                "libjpeg-turbo8": "*"
            },
            "source": "ubuntu"
        }
    },
    "libk5crypto3": {
        "1.17.0": {
            "dependencies": {
                "libc6": "*",
                "libkrb5support0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libkadm5clnt-mit11": {
        "1.17.0": {
            "dependencies": {
                "libc6": "*",
                "libcom-err2": "*",
                "libgssapi-krb5-2": "*",
                "libgssrpc4": "*",
                "libk5crypto3": "*",
                "libkrb5-3": "*",
                "libkrb5support0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libkadm5srv-mit11": {
        "1.17.0": {
            "dependencies": {
                "libc6": "*",
                "libcom-err2": "*",
                "libgssapi-krb5-2": "*",
                "libgssrpc4": "*",
                "libk5crypto3": "*",
                "libkdb5-9": "*",
                "libkrb5-3": "*",
                "libkrb5support0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libkdb5-9": {
        "1.17.0": {
            "dependencies": {
                "libc6": "*",
                "libcom-err2": "*",
                "libgssrpc4": "*",
                "libk5crypto3": "*",
                "libkrb5-3": "*",
                "libkrb5support0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libkeyutils1": {
        "1.6.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libkrb5-3": {
        "1.17.0": {
            "dependencies": {
                "libc6": "*",
                "libcom-err2": "*",
                "libk5crypto3": "*",
                "libkeyutils1": "*",
                "libkrb5support0": "*",
                "libssl1.1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libkrb5-dev": {
        "1.17.0": {
            "dependencies": {
                "krb5-multidev": "*"
            },
            "source": "ubuntu"
        }
    },
    "libkrb5support0": {
        "1.17.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libllvm10": {
        "10.0.0": {
            "dependencies": {
                "libc6": "*",
                "libedit2": "*",
                "libffi7": "*",
                "libgcc-s1": "*",
                "libstdc++6": "*",
                "libtinfo6": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libllvm11": {
        "11.0.0": {
            "dependencies": {
                "libc6": "*",
                "libedit2": "*",
                "libffi7": "*",
                "libgcc-s1": "*",
                "libstdc++6": "*",
                "libtinfo6": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "liblzma5": {
        "5.2.4": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libminiupnpc-dev": {
        "2.1.20190824": {
            "dependencies": {
                "libminiupnpc17": "*"
            },
            "source": "ubuntu"
        }
    },
    "libminiupnpc17": {
        "2.1.20190824": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libmount1": {
        "2.34.0": {
            "dependencies": {
                "libblkid1": "*",
                "libc6": "*",
                "libselinux1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libmpc3": {
        "1.1.0": {
            "dependencies": {
                "libc6": "*",
                "libgmp10": "*",
                "libmpfr6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libmpdec2": {
        "2.4.2": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libmpfr6": {
        "4.0.2": {
            "dependencies": {
                "libc6": "*",
                "libgmp10": "*"
            },
            "source": "ubuntu"
        }
    },
    "libmtdev1": {
        "1.1.5": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libnatpmp-dev": {
        "20150609.0.0": {
            "dependencies": {
                "libnatpmp1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libnatpmp1": {
        "20150609.0.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libncursesw6": {
        "6.2.0": {
            "dependencies": {
                "libc6": "*",
                "libtinfo6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libnettle7": {
        "3.5.1+really3.5.1": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libnorm-dev": {
        "1.5.8+dfsg2": {
            "dependencies": {
                "libnorm1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libnorm1": {
        "1.5.8+dfsg2": {
            "dependencies": {
                "libc6": "*",
                "libgcc-s1": "*",
                "libstdc++6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libnvpair1linux": {
        "0.8.3": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libopengl-dev": {
        "1.3.2": {
            "dependencies": {
                "libopengl0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libopengl0": {
        "1.3.2": {
            "dependencies": {
                "libc6": "*",
                "libglvnd0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libp11-kit0": {
        "0.23.20": {
            "dependencies": {
                "libc6": "*",
                "libffi7": "*"
            },
            "source": "ubuntu"
        }
    },
    "libpciaccess0": {
        "0.16.0": {
            "dependencies": {
                "libc6": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libpcre2-16-0": {
        "10.34.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libpcre2-8-0": {
        "10.34.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libpcre3": {
        "8.39.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libperl5.30": {
        "5.30.0": {
            "dependencies": {
                "libbz2-1.0": "*",
                "libc6": "*",
                "libcrypt1": "*",
                "libdb5.3": "*",
                "libgdbm-compat4": "*",
                "libgdbm6": "*",
                "perl-modules-5.30": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libpfm4": {
        "4.10.1+git20": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libpgm-5.2-0": {
        "5.2.122-dfsg": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libpgm-dev": {
        "5.2.122-dfsg": {
            "dependencies": {
                "libpgm-5.2-0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libpipeline1": {
        "1.5.2": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libpng16-16": {
        "1.6.37": {
            "dependencies": {
                "libc6": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libpthread-stubs0-dev": {
        "0.4.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "libpython3-stdlib": {
        "3.8.2": {
            "dependencies": {
                "libpython3.8-stdlib": "*"
            },
            "source": "ubuntu"
        }
    },
    "libpython3.8-minimal": {
        "3.8.5": {
            "dependencies": {
                "libc6": "*",
                "libssl1.1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libpython3.8-stdlib": {
        "3.8.5": {
            "dependencies": {
                "libbz2-1.0": "*",
                "libc6": "*",
                "libcrypt1": "*",
                "libdb5.3": "*",
                "libffi7": "*",
                "liblzma5": "*",
                "libmpdec2": "*",
                "libncursesw6": "*",
                "libpython3.8-minimal": "*",
                "libreadline8": "*",
                "libsqlite3-0": "*",
                "libtinfo6": "*",
                "libuuid1": "*",
                "mime-support": "*"
            },
            "source": "ubuntu"
        }
    },
    "libqrencode-dev": {
        "4.0.2": {
            "dependencies": {
                "libqrencode4": "*"
            },
            "source": "ubuntu"
        }
    },
    "libqrencode4": {
        "4.0.2": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libqt5concurrent5": {
        "5.12.8+dfsg": {
            "dependencies": {
                "libc6": "*",
                "libgcc-s1": "*",
                "libqt5core5a": "*",
                "libstdc++6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libqt5core5a": {
        "5.12.8+dfsg": {
            "dependencies": {
                "libc6": "*",
                "libdouble-conversion3": "*",
                "libgcc-s1": "*",
                "libglib2.0-0": "*",
                "libicu66": "*",
                "libpcre2-16-0": "*",
                "libstdc++6": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libqt5dbus5": {
        "5.12.8+dfsg": {
            "dependencies": {
                "libc6": "*",
                "libdbus-1-3": "*",
                "libqt5core5a": "*",
                "libstdc++6": "*",
                "qtbase-abi-5-12-8": "*"
            },
            "source": "ubuntu"
        }
    },
    "libqt5gui5": {
        "5.12.8+dfsg": {
            "dependencies": {
                "fontconfig": "*",
                "libc6": "*",
                "libdrm2": "*",
                "libegl1": "*",
                "libfontconfig1": "*",
                "libfreetype6": "*",
                "libgbm1": "*",
                "libgcc-s1": "*",
                "libgl1": "*",
                "libglib2.0-0": "*",
                "libharfbuzz0b": "*",
                "libice6": "*",
                "libinput10": "*",
                "libjpeg8": "*",
                "libmtdev1": "*",
                "libpng16-16": "*",
                "libqt5core5a": "*",
                "libqt5dbus5": "*",
                "libqt5network5": "*",
                "libsm6": "*",
                "libstdc++6": "*",
                "libudev1": "*",
                "libx11-6": "*",
                "libx11-xcb1": "*",
                "libxcb-glx0": "*",
                "libxcb-icccm4": "*",
                "libxcb-image0": "*",
                "libxcb-keysyms1": "*",
                "libxcb-randr0": "*",
                "libxcb-render-util0": "*",
                "libxcb-render0": "*",
                "libxcb-shape0": "*",
                "libxcb-shm0": "*",
                "libxcb-sync1": "*",
                "libxcb-xfixes0": "*",
                "libxcb-xinerama0": "*",
                "libxcb-xinput0": "*",
                "libxcb-xkb1": "*",
                "libxcb1": "*",
                "libxkbcommon-x11-0": "*",
                "libxkbcommon0": "*",
                "libxrender1": "*",
                "qtbase-abi-5-12-8": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libqt5network5": {
        "5.12.8+dfsg": {
            "dependencies": {
                "libc6": "*",
                "libqt5core5a": "*",
                "libqt5dbus5": "*",
                "libssl1.1": "*",
                "libstdc++6": "*",
                "qtbase-abi-5-12-8": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libqt5printsupport5": {
        "5.12.8+dfsg": {
            "dependencies": {
                "libc6": "*",
                "libcups2": "*",
                "libqt5core5a": "*",
                "libqt5gui5": "*",
                "libqt5widgets5": "*",
                "libstdc++6": "*",
                "qtbase-abi-5-12-8": "*"
            },
            "source": "ubuntu"
        }
    },
    "libqt5sql5": {
        "5.12.8+dfsg": {
            "dependencies": {
                "libc6": "*",
                "libqt5core5a": "*",
                "libstdc++6": "*",
                "qtbase-abi-5-12-8": "*"
            },
            "source": "ubuntu"
        }
    },
    "libqt5test5": {
        "5.12.8+dfsg": {
            "dependencies": {
                "libc6": "*",
                "libgcc-s1": "*",
                "libqt5core5a": "*",
                "libstdc++6": "*",
                "qtbase-abi-5-12-8": "*"
            },
            "source": "ubuntu"
        }
    },
    "libqt5widgets5": {
        "5.12.8+dfsg": {
            "dependencies": {
                "libc6": "*",
                "libqt5core5a": "*",
                "libqt5gui5": "*",
                "libstdc++6": "*",
                "qtbase-abi-5-12-8": "*"
            },
            "source": "ubuntu"
        }
    },
    "libqt5xml5": {
        "5.12.8+dfsg": {
            "dependencies": {
                "libc6": "*",
                "libqt5core5a": "*",
                "libstdc++6": "*",
                "qtbase-abi-5-12-8": "*"
            },
            "source": "ubuntu"
        }
    },
    "libquadmath0": {
        "10.2.0": {
            "dependencies": {
                "gcc-10-base": "*",
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libreadline8": {
        "8.0.0": {
            "dependencies": {
                "libc6": "*",
                "libtinfo6": "*",
                "readline-common": "*"
            },
            "source": "ubuntu"
        }
    },
    "libselinux1": {
        "3.0.0": {
            "dependencies": {
                "libc6": "*",
                "libpcre2-8-0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libsensors-config": {
        "3.6.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "libsensors5": {
        "3.6.0": {
            "dependencies": {
                "libc6": "*",
                "libsensors-config": "*"
            },
            "source": "ubuntu"
        }
    },
    "libsm6": {
        "1.2.3": {
            "dependencies": {
                "libc6": "*",
                "libice6": "*",
                "libuuid1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libsodium-dev": {
        "1.0.18": {
            "dependencies": {
                "libsodium23": "*"
            },
            "source": "ubuntu"
        }
    },
    "libsodium23": {
        "1.0.18": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libsqlite3-0": {
        "3.31.1": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libsqlite3-dev": {
        "3.31.1": {
            "dependencies": {
                "libc6-dev": "*",
                "libsqlite3-0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libssl1.1": {
        "1.1.1-f": {
            "dependencies": {
                "debconf": "*",
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libstdc++6": {
        "10.2.0": {
            "dependencies": {
                "gcc-10-base": "*",
                "libc6": "*",
                "libgcc-s1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libsystemd0": {
        "245.4.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "libtasn1-6": {
        "4.16.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libtinfo6": {
        "6.2.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libudev1": {
        "245.4.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libunistring2": {
        "0.9.10": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libunivalue-dev": {
        "1.0.4": {
            "dependencies": {
                "libunivalue0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libunivalue0": {
        "1.0.4": {
            "dependencies": {
                "libc6": "*",
                "libgcc-s1": "*",
                "libstdc++6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libuuid1": {
        "2.34.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libuutil1linux": {
        "0.8.3": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libvulkan-dev": {
        "1.2.131+2": {
            "dependencies": {
                "libvulkan1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libvulkan1": {
        "1.2.131+2": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libwacom-common": {
        "1.3.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "libwacom2": {
        "1.3.0": {
            "dependencies": {
                "libc6": "*",
                "libglib2.0-0": "*",
                "libgudev-1.0-0": "*",
                "libwacom-common": "*"
            },
            "source": "ubuntu"
        }
    },
    "libwayland-client0": {
        "1.18.0": {
            "dependencies": {
                "libc6": "*",
                "libffi7": "*"
            },
            "source": "ubuntu"
        }
    },
    "libwayland-server0": {
        "1.18.0": {
            "dependencies": {
                "libc6": "*",
                "libffi7": "*"
            },
            "source": "ubuntu"
        }
    },
    "libx11-6": {
        "1.6.9": {
            "dependencies": {
                "libc6": "*",
                "libx11-data": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libx11-data": {
        "1.6.9": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "libx11-dev": {
        "1.6.9": {
            "dependencies": {
                "libx11-6": "*",
                "libxau-dev": "*",
                "libxcb1-dev": "*",
                "libxdmcp-dev": "*",
                "x11proto-dev": "*",
                "xtrans-dev": "*"
            },
            "source": "ubuntu"
        }
    },
    "libx11-xcb1": {
        "1.6.9": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "libx32gcc-s1": {
        "10.2.0": {
            "dependencies": {
                "gcc-10-base": "*",
                "libc6-x32": "*"
            },
            "source": "ubuntu"
        }
    },
    "libx32quadmath0": {
        "10.2.0": {
            "dependencies": {
                "gcc-10-base": "*",
                "libc6-x32": "*"
            },
            "source": "ubuntu"
        }
    },
    "libx32stdc++6": {
        "10.2.0": {
            "dependencies": {
                "gcc-10-base": "*",
                "libc6-x32": "*",
                "libx32gcc-s1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libx32z1": {
        "1.2.11+dfsg": {
            "dependencies": {
                "libc6-x32": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxau-dev": {
        "1.0.9": {
            "dependencies": {
                "libxau6": "*",
                "x11proto-core-dev": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxau6": {
        "1.0.9": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-dri2-0": {
        "1.14.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-dri3-0": {
        "1.14.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-glx0": {
        "1.14.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-icccm4": {
        "0.4.1": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-image0": {
        "0.4.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb-shm0": "*",
                "libxcb-util1": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-keysyms1": {
        "0.4.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-present0": {
        "1.14.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-randr0": {
        "1.14.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-render-util0": {
        "0.3.9": {
            "dependencies": {
                "libc6": "*",
                "libxcb-render0": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-render0": {
        "1.14.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-shape0": {
        "1.14.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-shm0": {
        "1.14.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-sync1": {
        "1.14.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-util1": {
        "0.4.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-xfixes0": {
        "1.14.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-xinerama0": {
        "1.14.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-xinput0": {
        "1.14.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb-xkb1": {
        "1.14.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb1": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb1": {
        "1.14.0": {
            "dependencies": {
                "libc6": "*",
                "libxau6": "*",
                "libxdmcp6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxcb1-dev": {
        "1.14.0": {
            "dependencies": {
                "libpthread-stubs0-dev": "*",
                "libxau-dev": "*",
                "libxcb1": "*",
                "libxdmcp-dev": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxdamage1": {
        "1.1.5": {
            "dependencies": {
                "libc6": "*",
                "libx11-6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxdmcp-dev": {
        "1.1.3": {
            "dependencies": {
                "libxdmcp6": "*",
                "x11proto-core-dev": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxdmcp6": {
        "1.1.3": {
            "dependencies": {
                "libbsd0": "*",
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxext-dev": {
        "1.3.4": {
            "dependencies": {
                "libx11-dev": "*",
                "libxext6": "*",
                "x11proto-core-dev": "*",
                "x11proto-xext-dev": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxext6": {
        "1.3.4": {
            "dependencies": {
                "libc6": "*",
                "libx11-6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxfixes3": {
        "5.0.3": {
            "dependencies": {
                "libc6": "*",
                "libx11-6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxkbcommon-x11-0": {
        "0.10.0": {
            "dependencies": {
                "libc6": "*",
                "libxcb-xkb1": "*",
                "libxcb1": "*",
                "libxkbcommon0": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxkbcommon0": {
        "0.10.0": {
            "dependencies": {
                "libc6": "*",
                "xkb-data": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxrender1": {
        "0.9.10": {
            "dependencies": {
                "libc6": "*",
                "libx11-6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxshmfence1": {
        "1.3.0": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libxxf86vm1": {
        "1.1.4": {
            "dependencies": {
                "libc6": "*",
                "libx11-6": "*",
                "libxext6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libzfs2linux": {
        "0.8.3": {
            "dependencies": {
                "libblkid1": "*",
                "libc6": "*",
                "libnvpair1linux": "*",
                "libssl1.1": "*",
                "libudev1": "*",
                "libuuid1": "*",
                "libuutil1linux": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libzfslinux-dev": {
        "0.8.3": {
            "dependencies": {
                "libnvpair1linux": "*",
                "libuutil1linux": "*",
                "libzfs2linux": "*",
                "libzpool2linux": "*"
            },
            "source": "ubuntu"
        }
    },
    "libzmq3-dev": {
        "4.3.2": {
            "dependencies": {
                "libkrb5-dev": "*",
                "libnorm-dev": "*",
                "libpgm-dev": "*",
                "libsodium-dev": "*",
                "libzmq5": "*"
            },
            "source": "ubuntu"
        }
    },
    "libzmq5": {
        "4.3.2": {
            "dependencies": {
                "libc6": "*",
                "libgcc-s1": "*",
                "libgssapi-krb5-2": "*",
                "libnorm1": "*",
                "libpgm-5.2-0": "*",
                "libsodium23": "*",
                "libstdc++6": "*"
            },
            "source": "ubuntu"
        }
    },
    "libzpool2linux": {
        "0.8.3": {
            "dependencies": {
                "libblkid1": "*",
                "libc6": "*",
                "libnvpair1linux": "*",
                "libudev1": "*",
                "libuuid1": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "libzstd1": {
        "1.4.4+dfsg": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    },
    "linux-libc-dev": {
        "5.4.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "lld-10": {
        "10.0.0": {
            "dependencies": {
                "libc6": "*",
                "libgcc-s1": "*",
                "libllvm10": "*",
                "libstdc++6": "*"
            },
            "source": "ubuntu"
        }
    },
    "llvm-10": {
        "10.0.0": {
            "dependencies": {
                "libc6": "*",
                "libgcc-s1": "*",
                "libllvm10": "*",
                "libpfm4": "*",
                "libstdc++6": "*",
                "libtinfo6": "*",
                "llvm-10-runtime": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "llvm-10-runtime": {
        "10.0.0": {
            "dependencies": {
                "binfmt-support": "*",
                "libc6": "*",
                "libgcc-s1": "*",
                "libllvm10": "*",
                "libstdc++6": "*",
                "libtinfo6": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "lsb-base": {
        "11.1.0-ubuntu2": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "mime-support": {
        "3.64.0-ubuntu1": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "mingw-w64-common": {
        "7.0.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "mingw-w64-i686-dev": {
        "7.0.0": {
            "dependencies": {
                "mingw-w64-common": "*"
            },
            "source": "ubuntu"
        }
    },
    "perl-base": {
        "5.30.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "perl-modules-5.30": {
        "5.30.0": {
            "dependencies": {
                "perl-base": "*"
            },
            "source": "ubuntu"
        }
    },
    "perl:any": {
        "5.30.0": {
            "dependencies": {
                "libperl5.30": "*",
                "perl-base": "*",
                "perl-modules-5.30": "*"
            },
            "source": "ubuntu"
        }
    },
    "python3": {
        "3.8.2": {
            "dependencies": {
                "libpython3-stdlib": "*",
                "python3.8": "*"
            },
            "source": "ubuntu"
        }
    },
    "python3.8": {
        "3.8.5": {
            "dependencies": {
                "libpython3.8-stdlib": "*",
                "mime-support": "*",
                "python3.8-minimal": "*"
            },
            "source": "ubuntu"
        }
    },
    "python3.8-minimal": {
        "3.8.5": {
            "dependencies": {
                "libexpat1": "*",
                "libpython3.8-minimal": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "qt5-qmake": {
        "5.12.8+dfsg": {
            "dependencies": {
                "qt5-qmake-bin": "*",
                "qtchooser": "*"
            },
            "source": "ubuntu"
        }
    },
    "qt5-qmake-bin": {
        "5.12.8+dfsg": {
            "dependencies": {
                "libc6": "*",
                "libgcc-s1": "*",
                "libstdc++6": "*"
            },
            "source": "ubuntu"
        }
    },
    "qtbase5-dev": {
        "5.12.8+dfsg": {
            "dependencies": {
                "libegl-dev": "*",
                "libgl-dev": "*",
                "libglu1-mesa-dev": "*",
                "libqt5concurrent5": "*",
                "libqt5core5a": "*",
                "libqt5dbus5": "*",
                "libqt5gui5": "*",
                "libqt5network5": "*",
                "libqt5printsupport5": "*",
                "libqt5sql5": "*",
                "libqt5test5": "*",
                "libqt5widgets5": "*",
                "libqt5xml5": "*",
                "libvulkan-dev": "*",
                "libxext-dev": "*",
                "qt5-qmake": "*",
                "qtbase5-dev-tools": "*",
                "qtchooser": "*"
            },
            "source": "ubuntu"
        }
    },
    "qtbase5-dev-tools": {
        "5.12.8+dfsg": {
            "dependencies": {
                "libc6": "*",
                "libgcc-s1": "*",
                "libqt5core5a": "*",
                "libqt5dbus5": "*",
                "libstdc++6": "*",
                "perl:any": "*",
                "qtbase-abi-5-12-8": "*",
                "qtchooser": "*",
                "zlib1g": "*"
            },
            "source": "ubuntu"
        }
    },
    "qtchooser": {
        "66.0.0": {
            "dependencies": {
                "libc6": "*",
                "libgcc-s1": "*",
                "libstdc++6": "*"
            },
            "source": "ubuntu"
        }
    },
    "readline-common": {
        "8.0.0": {
            "dependencies": {
                "dpkg": "*"
            },
            "source": "ubuntu"
        }
    },
    "sensible-utils": {
        "0.0.12+nmu1": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "tar": {
        "1.30.0+dfsg": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "tzdata": {
        "2021.0.0-a": {
            "dependencies": {
                "debconf": "*"
            },
            "source": "ubuntu"
        }
    },
    "ucf": {
        "3.38.0+nmu1": {
            "dependencies": {
                "coreutils": "*",
                "debconf": "*",
                "sensible-utils": "*"
            },
            "source": "ubuntu"
        }
    },
    "x11-common": {
        "7.7.0+19ubuntu14": {
            "dependencies": {
                "lsb-base": "*"
            },
            "source": "ubuntu"
        }
    },
    "x11proto-core-dev": {
        "2019.2.0": {
            "dependencies": {
                "x11proto-dev": "*"
            },
            "source": "ubuntu"
        }
    },
    "x11proto-dev": {
        "2019.2.0": {
            "dependencies": {
                "xorg-sgml-doctools": "*"
            },
            "source": "ubuntu"
        }
    },
    "x11proto-xext-dev": {
        "2019.2.0": {
            "dependencies": {
                "x11proto-dev": "*"
            },
            "source": "ubuntu"
        }
    },
    "xkb-data": {
        "2.29.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "xorg-sgml-doctools": {
        "1.11.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "xtrans-dev": {
        "1.4.0": {
            "dependencies": {},
            "source": "ubuntu"
        }
    },
    "zlib1g": {
        "1.2.11+dfsg": {
            "dependencies": {
                "libc6": "*"
            },
            "source": "ubuntu"
        }
    }
}"""
        self._gh_smoke_test("bitcoin", "bitcoin", "4a267057617a8aa6dc9793c4d711725df5338025", result_json)

    def __test_cmake(self):
        result_json = """"""
        self._gh_smoke_test("lifting-bits", "rellic", "9cf73b288a3d0c51d5de7e1060cba8656538596f", result_json)

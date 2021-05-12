from unittest import TestCase
from pathlib import Path
import os
import json
import urllib
import zipfile
from it_depends.dependencies import resolve

IT_DEPENDS_DIR: Path = Path(__file__).absolute().parent.parent
TESTS_DIR: Path = Path(__file__).absolute().parent
REPOS_FOLDER = TESTS_DIR / "repos"


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

        package_list = resolve(SNAPSHOT_FOLDER)
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
                "libdl": "~=2",
                "libnss_files": "~=2",
                "libtinfo": "~=6",
                "python-dateutil": "~=2.8.1",
                "tqdm": "~=4.48.0"
            },
            "is_source_package": true,
            "source": "pip"
        }
    },
    "cvss": {
        "2.2.0": {
            "dependencies": {
                "libdl": "~=2",
                "libnss_files": "~=2",
                "libtinfo": "~=6"
            },
            "source": "pip"
        }
    },
    "libdl": {
        "2.0.0": {
            "dependencies": {},
            "source": "native"
        }
    },
    "libnss_files": {
        "2.0.0": {
            "dependencies": {},
            "source": "native"
        }
    },
    "libtinfo": {
        "6.0.0": {
            "dependencies": {},
            "source": "native"
        }
    },
    "python-dateutil": {
        "2.8.1": {
            "dependencies": {
                "libdl": "~=2",
                "libnss_files": "~=2",
                "libtinfo": "~=6",
                "six": ">=1.5"
            },
            "source": "pip"
        }
    },
    "six": {
        "1.5.0": {
            "dependencies": {
                "libdl": "~=2",
                "libnss_files": "~=2",
                "libtinfo": "~=6"
            },
            "source": "pip"
        }
    },
    "tqdm": {
        "4.48.0": {
            "dependencies": {
                "libdl": "~=2",
                "libnss_files": "~=2",
                "libtinfo": "~=6"
            },
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
                "criterion": "^0.3.2",
                "rustc-std-workspace-core": "^1.0.0"
            },
            "source": "cargo"
        }
    },
    "aho-corasick": {
        "0.7.15": {
            "dependencies": {
                "doc-comment": "^0.3.1",
                "memchr": "^2.2.0"
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
            "dependencies": {
                "futures": "^0.3",
                "rustversion": "^1.0",
                "syn": "^1.0",
                "thiserror": "^1.0",
                "trybuild": "^1.0.19"
            },
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
    "autocfg": {
        "1.0.1": {
            "dependencies": {},
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
                "proptest": "^0.9.1",
                "proptest-derive": "^0.1.0",
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
                "quickcheck": "^1",
                "regex-automata": "^0.1.5",
                "serde": "^1.0.85",
                "ucd-parse": "^0.1.3",
                "unicode-segmentation": "^1.2.1"
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
                "cargo-test-macro": "*",
                "cargo-test-support": "*",
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
    "cc": {
        "1.0.67": {
            "dependencies": {
                "jobserver": "^0.1.16",
                "tempfile": "^3"
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
                "lazy_static": "^1.3",
                "regex": "^1",
                "strsim": "^0.8",
                "term_size": "^0.3.0",
                "textwrap": "^0.11.0",
                "unicode-width": "^0.1.4",
                "vec_map": "^0.8",
                "version-sync": "^0.8",
                "yaml-rust": "^0.3.5"
            },
            "source": "cargo"
        }
    },
    "commoncrypto": {
        "0.2.0": {
            "dependencies": {
                "clippy": "^0.0",
                "commoncrypto-sys": "^0.2.0",
                "hex": "^0.2"
            },
            "source": "cargo"
        }
    },
    "commoncrypto-sys": {
        "0.2.0": {
            "dependencies": {
                "clippy": "^0.0",
                "hex": "^0.2",
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
                "bencher": "^0.1",
                "cfg-if": "^1.0",
                "quickcheck": "^0.9",
                "rand": "^0.7"
            },
            "source": "cargo"
        }
    },
    "crossbeam-utils": {
        "0.8.2": {
            "dependencies": {
                "autocfg": "^1.0.0",
                "cfg-if": "^1",
                "lazy_static": "^1.4.0",
                "loom": "^0.4",
                "rand": "^0.8"
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
                "anyhow": "^1.0.31",
                "curl-sys": "^0.4.37",
                "libc": "^0.2.42",
                "mio": "^0.6",
                "mio-extras": "^2.0.3",
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
                "cc": "^1.0",
                "libc": "^0.2.2",
                "libnghttp2-sys": "^0.1.3",
                "libz-sys": "^1.0.18",
                "mesalink": "^1.1.0-cratesio",
                "openssl-sys": "^0.9",
                "pkg-config": "^0.3.3",
                "vcpkg": "^0.2",
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "either": {
        "1.6.1": {
            "dependencies": {
                "serde": "^1.0",
                "serde_json": "^1.0.0"
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
        }
    },
    "filetime": {
        "0.2.14": {
            "dependencies": {
                "cfg-if": "^1.0.0",
                "libc": "^0.2.27",
                "redox_syscall": "^0.2",
                "tempfile": "^3",
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
                "futures": "^0.1",
                "libc": "^0.2.65",
                "libz-sys": "^1.1.0",
                "miniz-sys": "^0.1.11",
                "miniz_oxide": "^0.4.0",
                "quickcheck": "^0.9",
                "rand": "^0.7",
                "tokio-io": "^0.1.11",
                "tokio-tcp": "^0.1.3",
                "tokio-threadpool": "^0.1.10"
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
                "proptest": "^0.9",
                "termcolor": "^1"
            },
            "source": "cargo"
        }
    },
    "generator": {
        "0.6.24": {
            "dependencies": {
                "cc": "^1.0",
                "libc": "^0.2",
                "log": "^0.4",
                "rustversion": "^1.0",
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
                "wasm-bindgen": "^0.2.62",
                "wasm-bindgen-test": "^0.3.18"
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
                "paste": "^1",
                "structopt": "^0.3",
                "tempfile": "^3.1.0",
                "thread-id": "^3.3.0",
                "time": "^0.1.39",
                "url": "^2.0"
            },
            "source": "cargo"
        }
    },
    "git2-curl": {
        "0.14.1": {
            "dependencies": {
                "civet": "^0.11",
                "conduit": "^0.8",
                "conduit-git-http-backend": "^0.8",
                "curl": "^0.4.33",
                "git2": "^0.13",
                "log": "^0.4",
                "tempfile": "^3.0",
                "url": "^2.0"
            },
            "source": "cargo"
        }
    },
    "glob": {
        "0.3.0": {
            "dependencies": {
                "tempdir": "^0.3"
            },
            "source": "cargo"
        }
    },
    "globset": {
        "0.4.6": {
            "dependencies": {
                "aho-corasick": "^0.7.3",
                "bstr": "^0.2.0",
                "fnv": "^1.0.6",
                "glob": "^0.3.0",
                "lazy_static": "^1",
                "log": "^0.4.5",
                "regex": "^1.1.5",
                "serde": "^1.0.104",
                "serde_json": "^1.0.45"
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
                "criterion": "^0.3",
                "faster-hex": "^0.4",
                "pretty_assertions": "^0.6",
                "rustc-hex": "^2.0",
                "serde": "^1.0",
                "serde_json": "^1.0",
                "version-sync": "^0.8"
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
            "dependencies": {
                "chrono": "^0.4",
                "rand": "^0.6",
                "time": "^0.1"
            },
            "source": "cargo"
        }
    },
    "idna": {
        "0.2.2": {
            "dependencies": {
                "assert_matches": "^1.3",
                "bencher": "^0.1",
                "matches": "^0.1",
                "rustc-test": "^0.3",
                "serde_json": "^1.0",
                "unicode-bidi": "^0.3",
                "unicode-normalization": "^0.1.17"
            },
            "source": "cargo"
        }
    },
    "ignore": {
        "0.4.17": {
            "dependencies": {
                "crossbeam-channel": "^0.5.0",
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
                "metrohash": "^1",
                "pretty_assertions": "^0.6",
                "proptest": "^0.9",
                "proptest-derive": "^0.1",
                "quickcheck": "^0.9",
                "rand": "^0.7",
                "rand_core": "^0.5.1",
                "rand_xoshiro": "^0.4",
                "rayon": "^1",
                "refpool": "^0.4",
                "serde": "^1",
                "serde_json": "^1",
                "sized-chunks": "^0.6",
                "typenum": "^1.12",
                "version_check": "^0.9"
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
                "futures": "^0.1",
                "libc": "^0.2.50",
                "num_cpus": "^1.0",
                "tempdir": "^0.3",
                "tokio-core": "^0.1",
                "tokio-process": "^0.2"
            },
            "source": "cargo"
        }
    },
    "lazy_static": {
        "1.4.0": {
            "dependencies": {
                "doc-comment": "^0.3.1",
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
                "cc": "^1.0.43",
                "libc": "^0.2",
                "libssh2-sys": "^0.2.19",
                "libz-sys": "^1.1.0",
                "openssl-sys": "^0.9",
                "pkg-config": "^0.3.7"
            },
            "source": "cargo"
        }
    },
    "libnghttp2-sys": {
        "0.1.6+1.43.0": {
            "dependencies": {
                "cc": "^1.0.24",
                "libc": "^0.2"
            },
            "source": "cargo"
        }
    },
    "libssh2-sys": {
        "0.2.21": {
            "dependencies": {
                "cc": "^1.0.25",
                "libc": "^0.2",
                "libz-sys": "^1.1.0",
                "openssl-sys": "^0.9.35",
                "pkg-config": "^0.3.11",
                "vcpkg": "^0.2"
            },
            "source": "cargo"
        }
    },
    "libz-sys": {
        "1.1.2": {
            "dependencies": {
                "cc": "^1.0.18",
                "cmake": "^0.1.44",
                "libc": "^0.2.43",
                "pkg-config": "^0.3.9",
                "vcpkg": "^0.2"
            },
            "source": "cargo"
        }
    },
    "llvm-ir": {
        "0.7.4": {
            "dependencies": {
                "either": "^1.5.2",
                "env_logger": "^0.6.2",
                "llvm-sys": "^90.2.0",
                "log": "^0.4.0"
            },
            "source": "cargo"
        }
    },
    "llvm-sys": {
        "110.0.0": {
            "dependencies": {
                "cc": "^1.0",
                "lazy_static": "^1.0",
                "libc": "^0.2",
                "regex": "^1.0",
                "semver": "^0.11"
            },
            "source": "cargo"
        }
    },
    "log": {
        "0.4.14": {
            "dependencies": {
                "cfg-if": "^1.0",
                "serde": "^1.0",
                "serde_test": "^1.0",
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
                "libc": "^0.2.18",
                "quickcheck": "^0.9"
            },
            "source": "cargo"
        }
    },
    "miniz_oxide": {
        "0.4.3": {
            "dependencies": {
                "adler": "^0.2.3",
                "autocfg": "^1.0",
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
                "rand": "^0.4",
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
                "crossbeam-utils": "^0.7.2",
                "lazy_static": "^1.0.0",
                "parking_lot": "^0.11",
                "regex": "^1.2.0"
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
                "hex": "^0.3",
                "lazy_static": "^1",
                "libc": "^0.2",
                "openssl-sys": "^0.9.60",
                "tempdir": "^0.3"
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
                "autocfg": "^1.0",
                "cc": "^1.0",
                "libc": "^0.2",
                "openssl-src": "^111.0.1",
                "pkg-config": "^0.3.9",
                "vcpkg": "^0.2.8"
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
    "pest": {
        "2.1.3": {
            "dependencies": {
                "serde": "^1.0.89",
                "serde_json": "^1.0.39",
                "ucd-trie": "^0.1.1"
            },
            "source": "cargo"
        }
    },
    "pkg-config": {
        "0.3.19": {
            "dependencies": {
                "lazy_static": "^1"
            },
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
                "serde_derive": "=1.0.107",
                "syn": "^1",
                "toml": "=0.5.2",
                "trybuild": "^1.0.19",
                "version_check": "^0.9"
            },
            "source": "cargo"
        }
    },
    "proc-macro-error-attr": {
        "1.0.4": {
            "dependencies": {
                "proc-macro2": "^1",
                "quote": "^1",
                "version_check": "^0.9"
            },
            "source": "cargo"
        }
    },
    "proc-macro2": {
        "1.0.24": {
            "dependencies": {
                "quote": "^1.0",
                "unicode-xid": "^0.2"
            },
            "source": "cargo"
        }
    },
    "quote": {
        "1.0.9": {
            "dependencies": {
                "proc-macro2": "^1.0.20",
                "rustversion": "^1.0",
                "trybuild": "^1.0.19"
            },
            "source": "cargo"
        }
    },
    "rand": {
        "0.8.3": {
            "dependencies": {
                "bincode": "^1.2.1",
                "libc": "^0.2.22",
                "log": "^0.4.4",
                "packed_simd_2": "^0.3.4",
                "rand_chacha": "^0.3.0",
                "rand_core": "^0.6.0",
                "rand_hc": "^0.3.0",
                "rand_pcg": "^0.3.0",
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
                "bincode": "^1",
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
                "lazy_static": "^1",
                "memchr": "^2.2.1",
                "quickcheck": "^0.8",
                "rand": "^0.6.5",
                "regex-syntax": "^0.6.22",
                "thread_local": "^1"
            },
            "source": "cargo"
        }
    },
    "regex-syntax": {
        "0.6.22": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "remove_dir_all": {
        "0.5.3": {
            "dependencies": {
                "doc-comment": "^0.3",
                "winapi": "^0.3"
            },
            "source": "cargo"
        }
    },
    "rustc-demangle": {
        "0.1.18": {
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
                "difference": "^2.0.0",
                "duct": "^0.9",
                "env_logger": "^0.5.0-rc.1",
                "log": "^0.4.1",
                "proptest": "^0.7.0",
                "serde": "^1.0",
                "serde_json": "^1.0",
                "tempdir": "^0.3.5"
            },
            "source": "cargo"
        }
    },
    "rustversion": {
        "1.0.4": {
            "dependencies": {
                "trybuild": "^1.0.35"
            },
            "source": "cargo"
        }
    },
    "ryu": {
        "1.0.5": {
            "dependencies": {
                "no-panic": "^0.1",
                "num_cpus": "^1.8",
                "rand": "^0.7",
                "rand_xorshift": "^0.2"
            },
            "source": "cargo"
        }
    },
    "same-file": {
        "1.0.6": {
            "dependencies": {
                "doc-comment": "^0.3",
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
                "serde": "^1.0",
                "serde_derive": "^1.0",
                "serde_json": "^1.0"
            },
            "source": "cargo"
        },
        "0.11.0": {
            "dependencies": {
                "diesel": "^1.1",
                "semver-parser": "^0.10.0",
                "serde": "^1.0",
                "serde_derive": "^1.0",
                "serde_json": "^1.0"
            },
            "source": "cargo"
        }
    },
    "semver-parser": {
        "0.10.2": {
            "dependencies": {
                "pest": "^2.1.0",
                "pest_generator": "^2.1",
                "proc-macro2": "^1.0"
            },
            "source": "cargo"
        },
        "0.7.0": {
            "dependencies": {},
            "source": "cargo"
        }
    },
    "serde": {
        "1.0.123": {
            "dependencies": {
                "serde_derive": "^1.0"
            },
            "source": "cargo"
        }
    },
    "serde_derive": {
        "1.0.123": {
            "dependencies": {
                "proc-macro2": "^1.0",
                "quote": "^1.0",
                "serde": "^1.0",
                "syn": "^1.0.60"
            },
            "source": "cargo"
        }
    },
    "serde_ignored": {
        "0.1.2": {
            "dependencies": {
                "serde": "^1.0",
                "serde_derive": "^1.0",
                "serde_json": "^1.0"
            },
            "source": "cargo"
        }
    },
    "serde_json": {
        "1.0.62": {
            "dependencies": {
                "automod": "^1.0",
                "indexmap": "^1.5",
                "itoa": "^0.4.3",
                "rustversion": "^1.0",
                "ryu": "^1.0",
                "serde": "^1.0.100",
                "serde_bytes": "^0.11",
                "serde_derive": "^1.0",
                "serde_stacker": "^0.1",
                "trybuild": "^1.0.19"
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
                "tempdir": "^0.3",
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
                "rustversion": "^1",
                "structopt-derive": "=0.4.14",
                "trybuild": "^1.0.5"
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
                "anyhow": "^1.0",
                "flate2": "^1.0",
                "insta": "^1.0",
                "proc-macro2": "^1.0.23",
                "quote": "^1.0",
                "rayon": "^1.0",
                "ref-cast": "^1.0",
                "regex": "^1.0",
                "reqwest": "^0.10",
                "syn-test-suite": "^0",
                "tar": "^0.4.16",
                "termcolor": "^1.0",
                "unicode-xid": "^0.2",
                "walkdir": "^2.1"
            },
            "source": "cargo"
        }
    },
    "tar": {
        "0.4.33": {
            "dependencies": {
                "filetime": "^0.2.8",
                "libc": "^0.2",
                "tempfile": "^3",
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
                "lipsum": "^0.6",
                "rand": "^0.6",
                "rand_xorshift": "^0.1",
                "term_size": "^0.3.0",
                "unicode-width": "^0.1.3",
                "version-sync": "^0.6"
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
                "criterion": "^0.3.0",
                "serde": "^1.0",
                "serde_test": "^1.0",
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
                "serde": "^1.0.97",
                "serde_derive": "^1.0",
                "serde_json": "^1.0"
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
    "ucd-trie": {
        "0.1.3": {
            "dependencies": {
                "lazy_static": "^1"
            },
            "source": "cargo"
        }
    },
    "unicode-bidi": {
        "0.3.4": {
            "dependencies": {
                "flame": "^0.1",
                "flamer": "^0.1",
                "matches": "^0.1",
                "serde": ">=0.8,<2.0",
                "serde_test": ">=0.8,<2.0"
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
            "dependencies": {
                "bencher": "^0.1",
                "quickcheck": "^0.7"
            },
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
                "bencher": "^0.1",
                "form_urlencoded": "^1.0.0",
                "idna": "^0.2.0",
                "matches": "^0.1",
                "percent-encoding": "^2.1.0",
                "serde": "^1.0",
                "serde_json": "^1.0"
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
    "vcpkg": {
        "0.2.11": {
            "dependencies": {
                "lazy_static": "^1",
                "tempdir": "^0.3.7"
            },
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
    "version_check": {
        "0.9.2": {
            "dependencies": {},
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
                "doc-comment": "^0.3",
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
                "libdl": "~=2",
                "libnss_files": "~=2",
                "libtinfo": "~=6"
            },
            "is_source_package": true,
            "source": "npm"
        }
    },
    "libdl": {
        "2.0.0": {
            "dependencies": {},
            "source": "native"
        }
    },
    "libnss_files": {
        "2.0.0": {
            "dependencies": {},
            "source": "native"
        }
    },
    "libtinfo": {
        "6.0.0": {
            "dependencies": {},
            "source": "native"
        }
    }
}"""
        self._gh_smoke_test("brix", "crypto-js", "971c31f0c931f913d22a76ed488d9216ac04e306", result_json)

    def test_autotools(self):
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

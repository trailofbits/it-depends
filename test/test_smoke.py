from unittest import TestCase
from pathlib import Path
import os
import json
import urllib
import zipfile
from it_depends.dependencies import resolve
from it_depends.db import DBPackageCache
IT_DEPENDS_DIR: Path = Path(__file__).absolute().parent.parent
TESTS_DIR: Path = Path(__file__).absolute().parent
REPOS_FOLDER = TESTS_DIR / "repos"


class TestSmoke(TestCase):
    maxDiff = None
    def setUp(self) -> None:
        if not os.path.exists(REPOS_FOLDER):
            os.makedirs(REPOS_FOLDER )

    def test_pip(self):
        SNAPSHOT_NAME = "cvedb"
        COMMIT= "7441dc0e238e31829891f85fd840d9e65cb629d8"
        URL = f"https://github.com/trailofbits/cvedb/archive/{COMMIT}.zip"

        SNAPSHOT_FOLDER = REPOS_FOLDER / (SNAPSHOT_NAME + "-" + COMMIT )
        SNAPSHOT_ZIP = SNAPSHOT_FOLDER.with_suffix(".zip")

        if not (SNAPSHOT_FOLDER).exists():
            urllib.request.urlretrieve(
                URL,
                SNAPSHOT_ZIP)
            with zipfile.ZipFile(SNAPSHOT_ZIP, 'r') as zip_ref:
                zip_ref.extractall(REPOS_FOLDER)

        package_list = resolve(SNAPSHOT_FOLDER)
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
        result_it_depends = json.dumps(package_list.to_obj(), indent=4, sort_keys=True)
        self.assertEqual(result_it_depends, result_json)
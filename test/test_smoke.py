from unittest import TestCase
from pathlib import Path
import json
import urllib
import zipfile
from it_depends.dependencies import resolve
IT_DEPENDS_DIR: Path = Path(__file__).absolute().parent.parent

class TestSmoke(TestCase):
    maxDiff = None

    def test_pip(self):
        PATH = IT_DEPENDS_DIR
        COMMIT= "7441dc0e238e31829891f85fd840d9e65cb629d8"
        URL = f"https://github.com/trailofbits/cvedb/archive/{COMMIT}.zip"
        SNAPSHOT_NAME = "cvedb"
        SNAPSHOT_FOLDER = PATH / "test" / SNAPSHOT_NAME
        SNAPSHOT_ZIP = SNAPSHOT_FOLDER.with_suffix(".zip")
        if not (SNAPSHOT_FOLDER).exists():
            urllib.request.urlretrieve(
                URL,
                SNAPSHOT_ZIP)
            with zipfile.ZipFile(SNAPSHOT_ZIP, 'r') as zip_ref:
                zip_ref.extractall(SNAPSHOT_FOLDER)
        print (SNAPSHOT_FOLDER)
        package_list = resolve(SNAPSHOT_FOLDER / f"{SNAPSHOT_NAME}-{COMMIT}")
        result_json = """{
    "cvedb": {
        "0.0.4": {
            "dependencies": {
                "cvss": "~=2.2",
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
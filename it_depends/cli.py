import os
import argparse
import json
import sys
from typing import Iterable, Optional

from .db import DEFAULT_DB_PATH, DBPackageCache
from .dependencies import CLASSIFIERS_BY_NAME, resolve


def main(argv: Optional[Iterable[str]] = None) -> int:
    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(description="a source code dependency analyzer")

    parser.add_argument("PATH", nargs="?", type=str, default=".", help="path to the directory to analyze")
    parser.add_argument("--list", "-l", action="store_true", help="list available package classifiers")
    parser.add_argument("--database", "-db", type=str, nargs="?", default=DEFAULT_DB_PATH,
                        help="alternative path to load/store the database, or \":memory:\" to cache all results in "
                             f"memory rather than reading/writing to disk (default is {DEFAULT_DB_PATH!s})")

    args = parser.parse_args(argv[1:])

    if args.list:
        sys.stdout.flush()
        sys.stderr.write(f"Available classifiers for {os.path.abspath(args.PATH)}:\n")
        sys.stderr.flush()
        for name, classifier in sorted(CLASSIFIERS_BY_NAME.items()):
            sys.stdout.write(name + " "*(12-len(name)))
            sys.stdout.flush()
            available = classifier.is_available()
            if not available:
                sys.stderr.write(f"\tnot available: {available.reason}")
                sys.stderr.flush()
            elif not classifier.can_classify(args.PATH):
                sys.stderr.write("\tincompatible with this path")
                sys.stderr.flush()
            sys.stdout.write("\n")
            sys.stdout.flush()
        return 0

    with DBPackageCache(args.database) as cache:
        package_list = resolve(args.PATH, cache)
        print(json.dumps(package_list.to_obj(), indent=4))
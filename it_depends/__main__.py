import os
import argparse
import json
import sys

from .dependencies import CLASSIFIERS_BY_NAME, DependencyResolver, resolve


def main():
    parser = argparse.ArgumentParser(description="a source code dependency analyzer")

    parser.add_argument("PATH", nargs="?", type=str, default=".", help="path to the directory to analyze")
    parser.add_argument("--list", "-l", action="store_true", help="list available package classifiers")

    args = parser.parse_args()

    if args.list:
        sys.stdout.flush()
        sys.stderr.write(f"Available classifiers for {os.path.abspath(args.PATH)}:\n")
        sys.stderr.flush()
        for name, classifier in sorted(CLASSIFIERS_BY_NAME.items()):
            sys.stdout.write(name + ' '*(12-len(name)))
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
        exit(0)

    package_list = resolve(args.PATH)
    print(json.dumps(package_list.to_obj(), indent=4))


if __name__ == "__main__":
    main()

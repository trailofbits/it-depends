import argparse
import json
import sys

from .dependencies import CLASSIFIERS_BY_NAME


def main():
    parser = argparse.ArgumentParser(description="a source code dependency analyzer")

    parser.add_argument("PATH", nargs="?", type=str, default=".", help="path to the directory to analyze")
    parser.add_argument("--list", "-l", action="store_true", help="list available package classifiers")

    args = parser.parse_args()

    if args.list:
        sys.stdout.flush()
        sys.stderr.write("Available classifiers:\n")
        sys.stderr.write("======================\n")
        sys.stderr.flush()
        for name, classifier in sorted(CLASSIFIERS_BY_NAME.items()):
            sys.stdout.write(name)
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

    deps = []
    for classifier in CLASSIFIERS_BY_NAME.values():
        if classifier.is_available() and classifier.can_classify(args.PATH):
            with classifier.classify(args.PATH) as resolver:
                deps.extend(resolver)
    dep_objs = [dep.to_obj() for dep in deps]
    print(json.dumps(dep_objs, indent=4))


if __name__ == "__main__":
    main()

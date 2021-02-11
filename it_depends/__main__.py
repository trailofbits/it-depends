import argparse
import json

from .pip import get_dependencies


def main():
    parser = argparse.ArgumentParser(description="a source code dependency analyzer")

    parser.add_argument("PATH", nargs="?", type=str, default=".", help="path to the directory to analyze")

    args = parser.parse_args()

    deps = get_dependencies(args.PATH)
    dep_objs = [dep.to_obj() for dep in deps]
    print(json.dumps(dep_objs, indent=4))


if __name__ == "__main__":
    main()

import os
import argparse
from contextlib import contextmanager
import json
import sys
from typing import Iterator, Optional, Sequence, TextIO
from .utils import show_graph
from .db import DEFAULT_DB_PATH, DBPackageCache
from .dependencies import resolvers, resolve, SourceRepository


@contextmanager
def no_stdout() -> Iterator[TextIO]:
    """A context manager that redirects STDOUT to STDERR"""
    saved_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield saved_stdout
    finally:
        sys.stdout = saved_stdout


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(description="a source code dependency analyzer")

    parser.add_argument("PATH", nargs="?", type=str, default=".", help="path to the directory to analyze")
    parser.add_argument("--list", "-l", action="store_true", help="list available package classifiers")
    parser.add_argument("--database", "-db", type=str, nargs="?", default=DEFAULT_DB_PATH,
                        help="alternative path to load/store the database, or \":memory:\" to cache all results in "
                             f"memory rather than reading/writing to disk (default is {DEFAULT_DB_PATH!s})")
    parser.add_argument("--output-format", "-f", choices=("json", "dot", "html"), default="json",
                        help="how the output should be formatted (default is JSON)")
    parser.add_argument("--depth-limit", "-d", type=int, default=-1,
                        help="depth limit for recursively solving dependencies (default is -1 to resolve all "
                             "dependencies)")
    parser.add_argument("--max-workers", "-j", type=int, default=None, help="maximum number of jobs to run concurrently"
                                                                            " (default is # of CPUs)")

    args = parser.parse_args(argv[1:])

    if args.list:
        sys.stdout.flush()
        sys.stderr.write(f"Available resolvers for {os.path.abspath(args.PATH)}:\n")
        sys.stderr.flush()
        for name, classifier in sorted((c.name, c) for c in resolvers()):
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

    with no_stdout() as real_stdout:
        with DBPackageCache(args.database) as cache:
            # TODO: Add support for searching by package name
            repo = SourceRepository(args.PATH)

            package_list = resolve(repo, cache=cache, depth_limit=args.depth_limit, max_workers=args.max_workers)

            if args.output_format == "dot":
                real_stdout.write(cache.to_dot(package_list.source_packages).source)
            if args.output_format == "html":
                show_graph(package_list.to_obj())
            elif args.output_format == "json":
                # assume JSON
                real_stdout.write(json.dumps(package_list.to_obj(), indent=4))
            else:
                raise NotImplementedError(f"TODO: Implement output format {args.output_format}")

    return 0

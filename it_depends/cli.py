import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from typing import Iterator, Optional, Sequence, TextIO, Union
import webbrowser

from sqlalchemy.exc import OperationalError

from .db import DEFAULT_DB_PATH, DBPackageCache
from .dependencies import Dependency, resolvers, resolver_by_name, resolve, SourceRepository
from .html import graph_to_html


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

    parser.add_argument("PATH_OR_NAME", nargs="?", type=str, default=".",
                        help="path to the directory to analyze, or a package name in the form of "
                             "RESOLVER_NAME:PACKAGE_NAME[@OPTIONAL_VERSION], where RESOLVER_NAME is a resolver listed "
                             "in `it-depends --list`. For example: \"pip:numpy\", \"apt:libc6@2.31\", or "
                             "\"npm:lodash@>=4.17.0\".")
    parser.add_argument("--list", "-l", action="store_true", help="list available package resolver")
    parser.add_argument("--database", "-db", type=str, nargs="?", default=DEFAULT_DB_PATH,
                        help="alternative path to load/store the database, or \":memory:\" to cache all results in "
                             f"memory rather than reading/writing to disk (default is {DEFAULT_DB_PATH!s})")
    parser.add_argument("--output-format", "-f", choices=("json", "dot", "html"), default="json",
                        help="how the output should be formatted (default is JSON)")
    parser.add_argument("--output-file", "-o", type=str, default=None, help="path to the output file; default is to "
                                                                            "write output to STDOUT")
    parser.add_argument("--force", action="store_true", help="force overwriting the output file even if it already "
                                                             "exists")
    parser.add_argument("--all-versions", action="store_true",
                        help="for `--output-format html`, this option will emit all package versions that satisfy each "
                             "dependency")
    parser.add_argument("--depth-limit", "-d", type=int, default=-1,
                        help="depth limit for recursively solving dependencies (default is -1 to resolve all "
                             "dependencies)")
    parser.add_argument("--max-workers", "-j", type=int, default=None, help="maximum number of jobs to run concurrently"
                                                                            " (default is # of CPUs)")

    args = parser.parse_args(argv[1:])

    repo_path = Path(args.PATH_OR_NAME)
    try:
        dependency: Optional[Dependency] = Dependency.from_string(args.PATH_OR_NAME)
    except ValueError as e:
        if str(e).endswith("is not a known resolver") and not repo_path.exists():
            sys.stderr.write(f"Unknown resolver: {args.PATH_OR_NAME}\n\n")
            return 1
        dependency = None
    if dependency is None or repo_path.exists():
        source_repo: Optional[SourceRepository] = SourceRepository(args.PATH_OR_NAME)
        dependency = None
    else:
        source_repo = None

    if args.list:
        sys.stdout.flush()
        sys.stderr.write(f"Available resolvers for {repo_path.absolute()}:\n")
        sys.stderr.flush()
        for name, classifier in sorted((c.name, c) for c in resolvers()):
            sys.stdout.write(name + " "*(12-len(name)))
            sys.stdout.flush()
            available = classifier.is_available()
            if not available:
                sys.stderr.write(f"\tnot available: {available.reason}")
                sys.stderr.flush()
            elif source_repo is not None and \
                    not classifier.can_resolve_from_source(SourceRepository(args.PATH_OR_NAME)):
                sys.stderr.write("\tincompatible with this path")
                sys.stderr.flush()
            elif dependency is not None and dependency.source != classifier.name:
                sys.stderr.write("\tincompatible with this package specifier")
            else:
                sys.stderr.write("\tenabled")
                sys.stderr.flush()

            sys.stdout.write("\n")
            sys.stdout.flush()
        return 0

    try:
        output_file = None
        with no_stdout() as real_stdout:
            if args.output_file is None or args.output_file == "-":
                output_file = real_stdout
            elif not args.force and Path(args.output_file).exists():
                sys.stderr.write(f"{args.output_file} already exists!\nRe-run with `--force` to overwrite the file.\n")
                return 1
            else:
                output_file = open(args.output_file, "w")
            with DBPackageCache(args.database) as cache:
                # TODO: Add support for searching by package name
                if source_repo is None:
                    repo: Union[SourceRepository, Dependency] = dependency  # type: ignore
                else:
                    repo = source_repo
                package_list = resolve(repo, cache=cache, depth_limit=args.depth_limit, max_workers=args.max_workers)
                if not package_list:
                    sys.stderr.write(f"Try --list to check for available resolvers for {args.PATH_OR_NAME}\n")
                    sys.stderr.flush()

                if args.output_format == "dot":
                    output_file.write(cache.to_dot(package_list.source_packages).source)
                if args.output_format == "html":
                    output_file.write(graph_to_html(package_list, collapse_versions=not args.all_versions))
                    if output_file is not real_stdout:
                        output_file.flush()
                        webbrowser.open(output_file.name)
                elif args.output_format == "json":
                    output_file.write(json.dumps(package_list.to_obj(), indent=4))
                else:
                    raise NotImplementedError(f"TODO: Implement output format {args.output_format}")
    except OperationalError as e:
        sys.stderr.write(f"Database error: {e!r}\n\nThis can occur if your database was created with an older version "
                         f"of it-depends and was unable to be updated. If you remove {args.database} and try again, "
                         "the database will automatically be rebuilt from scratch.")
        return 1
    finally:
        if output_file is not None and output_file != sys.stdout:
            sys.stderr.write(f"Output saved to {output_file.name}\n")
            output_file.close()

    return 0

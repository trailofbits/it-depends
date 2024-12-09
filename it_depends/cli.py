import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from typing import Iterator, Optional, Sequence, TextIO, Union
import webbrowser

from sqlalchemy.exc import OperationalError

from .audit import vulnerabilities
from .db import DEFAULT_DB_PATH, DBPackageCache
from .dependencies import Dependency, resolvers, resolve, SourceRepository
from .it_depends import version as it_depends_version
from .html import graph_to_html
from .sbom import package_to_cyclonedx, cyclonedx_to_json


@contextmanager
def no_stdout() -> Iterator[TextIO]:
    """A context manager that redirects STDOUT to STDERR"""
    saved_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield saved_stdout
    finally:
        sys.stdout = saved_stdout


def parse_path_or_package_name(
    path_or_name: str,
) -> Union[SourceRepository, Dependency]:
    repo_path = Path(path_or_name)
    try:
        dependency: Optional[Dependency] = Dependency.from_string(path_or_name)
    except ValueError as e:
        if str(e).endswith("is not a known resolver") and not repo_path.exists():
            raise ValueError(f"Unknown resolver: {path_or_name}")
        dependency = None
    if dependency is None or repo_path.exists():
        return SourceRepository(path_or_name)
    else:
        return dependency


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv is None:
        argv = sys.argv

    parser = argparse.ArgumentParser(description="a source code dependency analyzer")

    parser.add_argument(
        "PATH_OR_NAME",
        nargs="?",
        type=str,
        default=".",
        help="path to the directory to analyze, or a package name in the form of "
        "RESOLVER_NAME:PACKAGE_NAME[@OPTIONAL_VERSION], where RESOLVER_NAME is a resolver listed "
        'in `it-depends --list`. For example: "pip:numpy", "apt:libc6@2.31", or '
        '"npm:lodash@>=4.17.0".',
    )

    parser.add_argument(
        "--audit",
        "-a",
        action="store_true",
        help="audit packages for known vulnerabilities using " "Google OSV",
    )
    parser.add_argument("--list", "-l", action="store_true", help="list available package resolver")
    parser.add_argument(
        "--database",
        "-db",
        type=str,
        nargs="?",
        default=DEFAULT_DB_PATH,
        help='alternative path to load/store the database, or ":memory:" to cache all results in '
        f"memory rather than reading/writing to disk (default is {DEFAULT_DB_PATH!s})",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="clears the database specified by `--database` "
        "(equivalent to deleting the database file)",
    )
    parser.add_argument(
        "--compare",
        "-c",
        nargs="?",
        type=str,
        help="compare PATH_OR_NAME to another package specified according to the same rules as "
        "PATH_OR_NAME; this option will override the --output-format option and will instead "
        "output a floating point similarity metric. By default, the metric will be in the range"
        "[0, ∞), with zero meaning that the dependency graphs are identical. For a metric in the "
        "range [0, 1], see the `--normalize` option.",
    )
    parser.add_argument(
        "--normalize",
        "-n",
        action="store_true",
        help="Used in conjunction with `--compare`, this will change the output metric to be in the "
        "range [0, 1] where 1 means the graphs are identical and 0 means the graphs are as "
        "different as possible.",
    )
    parser.add_argument(
        "--output-format",
        "-f",
        choices=("json", "dot", "html", "cyclonedx"),
        default="json",
        help="how the output should be formatted (default is JSON)",
    )
    parser.add_argument(
        "--output-file",
        "-o",
        type=str,
        default=None,
        help="path to the output file; default is to " "write output to STDOUT",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="force overwriting the output file even if it already " "exists",
    )
    parser.add_argument(
        "--all-versions",
        action="store_true",
        help="for `--output-format html`, this option will emit all package versions that satisfy each "
        "dependency",
    )
    parser.add_argument(
        "--depth-limit",
        "-d",
        type=int,
        default=-1,
        help="depth limit for recursively solving dependencies (default is -1 to resolve all "
        "dependencies)",
    )
    parser.add_argument(
        "--max-workers",
        "-j",
        type=int,
        default=None,
        help="maximum number of jobs to run concurrently" " (default is # of CPUs)",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="store_true",
        help="print it-depends' version and exit",
    )

    args = parser.parse_args(argv[1:])

    if args.version:
        sys.stderr.write("it-depends version ")
        sys.stderr.flush()
        version = it_depends_version()
        sys.stdout.write(str(version))
        sys.stdout.flush()
        sys.stderr.write("\n")
        return 0

    try:
        repo = parse_path_or_package_name(args.PATH_OR_NAME)

        if args.compare is not None:
            to_compare: Optional[Union[SourceRepository, Dependency]] = parse_path_or_package_name(
                args.compare
            )
        else:
            to_compare = None
    except ValueError as e:
        sys.stderr.write(str(e))
        sys.stderr.write("\n\n")
        return 1

    if args.clear_cache:
        db_path = Path(args.database)
        if db_path.exists():
            if sys.stderr.isatty() and sys.stdin.isatty():
                while True:
                    if args.database != DEFAULT_DB_PATH:
                        sys.stderr.write(f"Cache file: {db_path.absolute()}\n")
                    sys.stderr.write(
                        "Deleting the cache will require all past resoltuions to be recalculated, which "
                        "can be slow.\nAre you sure? [yN] "
                    )
                    try:
                        choice = input("").lower().strip()
                    except KeyboardInterrupt:
                        return 1
                    if choice == "y":
                        db_path.unlink()
                        sys.stderr.write("Cache cleared.\n")
                        break
                    elif choice == "n" or choice == "":
                        break
            else:
                db_path.unlink()
                sys.stderr.write("Cache cleared.\n")

    if args.list:
        sys.stdout.flush()
        if isinstance(repo, SourceRepository):
            path = repo.path.absolute()
        else:
            path = args.PATH_OR_NAME
        sys.stderr.write(f"Available resolvers for {path}:\n")
        sys.stderr.flush()
        for name, classifier in sorted((c.name, c) for c in resolvers()):
            sys.stdout.write(name + " " * (12 - len(name)))
            sys.stdout.flush()
            available = classifier.is_available()
            if not available:
                sys.stderr.write(f"\tnot available: {available.reason}")
                sys.stderr.flush()
            elif isinstance(repo, SourceRepository) and not classifier.can_resolve_from_source(
                repo
            ):
                sys.stderr.write("\tincompatible with this path")
                sys.stderr.flush()
            elif isinstance(repo, Dependency) and repo.source != classifier.name:
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
                sys.stderr.write(
                    f"{args.output_file} already exists!\nRe-run with `--force` to overwrite the file.\n"
                )
                return 1
            else:
                output_file = open(args.output_file, "w")
            with DBPackageCache(args.database) as cache:
                try:
                    package_list = resolve(
                        repo,
                        cache=cache,
                        depth_limit=args.depth_limit,
                        max_workers=args.max_workers,
                    )
                except ValueError as e:
                    if not args.clear_cache or args.PATH_OR_NAME.strip():
                        sys.stderr.write(f"{e!s}\n")
                    return 1
                if not package_list:
                    sys.stderr.write(
                        f"Try --list to check for available resolvers for {args.PATH_OR_NAME}\n"
                    )
                    sys.stderr.flush()

                # TODO: Should the cache be updated instead????
                if args.audit:
                    package_list = vulnerabilities(package_list)

                if to_compare is not None:
                    to_compare_list = resolve(
                        to_compare,
                        cache=cache,
                        depth_limit=args.depth_limit,
                        max_workers=args.max_workers,
                    )
                    output_file.write(
                        str(
                            package_list.to_graph().distance_to(
                                to_compare_list.to_graph(), normalize=args.normalize
                            )
                        )
                    )
                    output_file.write("\n")
                elif args.output_format == "dot":
                    output_file.write(cache.to_dot(package_list.source_packages).source)
                elif args.output_format == "html":
                    output_file.write(
                        graph_to_html(package_list, collapse_versions=not args.all_versions)
                    )
                    if output_file is not real_stdout:
                        output_file.flush()
                        webbrowser.open(output_file.name)
                elif args.output_format == "json":
                    output_file.write(json.dumps(package_list.to_obj(), indent=4))
                elif args.output_format == "cyclonedx":
                    bom = None
                    for p in package_list:
                        bom = package_to_cyclonedx(p, packages=package_list, bom=bom, only_latest=True)
                    output_file.write(cyclonedx_to_json(bom))
                else:
                    raise NotImplementedError(f"TODO: Implement output format {args.output_format}")
    except OperationalError as e:
        sys.stderr.write(
            f"Database error: {e!r}\n\nThis can occur if your database was created with an older version "
            f"of it-depends and was unable to be updated. If you remove {args.database} or run "
            "`it-depends --clear-cache` and try again, the database will automatically be rebuilt from "
            "scratch."
        )
        return 1
    finally:
        if output_file is not None and output_file != sys.stdout:
            sys.stderr.write(f"Output saved to {output_file.name}\n")
            output_file.close()

    return 0

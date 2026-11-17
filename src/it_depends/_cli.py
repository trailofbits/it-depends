"""Command-line interface for it-depends."""

from __future__ import annotations

import json
import os
import sys
import webbrowser
from pathlib import Path
from sqlite3 import OperationalError
from typing import TYPE_CHECKING

from it_depends._commands import CommandSession
from it_depends._dashboard import make_reporter
from it_depends._output import PlainReporter, third_party_reporting
from it_depends.audit import vulnerabilities
from it_depends.config import Settings
from it_depends.db import DBPackageCache
from it_depends.dependencies import Dependency, SourceRepository, resolve, resolve_sbom, resolvers
from it_depends.html import graph_to_html
from it_depends.maintenance import check_maintenance_status
from it_depends.reporting import Severity, diagnostic, reporting_session
from it_depends.sbom import cyclonedx_to_json
from it_depends.trust import TrustError, require_trust

if TYPE_CHECKING:
    from collections.abc import Callable

    from it_depends.cache import PackageRepository


def parse_path_or_package_name(
    path_or_name: str,
) -> SourceRepository | Dependency:
    repo_path = Path(path_or_name)
    try:
        dependency: Dependency | None = Dependency.from_string(path_or_name)
    except ValueError as e:
        if str(e).endswith("is not a known resolver") and not repo_path.exists():
            msg = f"Unknown resolver: {path_or_name}"
            raise ValueError(msg) from e
        dependency = None
    if dependency is None or repo_path.exists():
        return SourceRepository(path_or_name)
    return dependency


def _parse_targets(
    settings: Settings,
) -> tuple[SourceRepository | Dependency, SourceRepository | Dependency | None]:
    try:
        repo = parse_path_or_package_name(settings.target)

        if settings.compare != "":
            to_compare: SourceRepository | Dependency | None = parse_path_or_package_name(settings.compare)
        else:
            to_compare = None
    except ValueError as e:
        msg = str(e)
        diagnostic(Severity.ERROR, __name__, msg, exception=True)
        sys.exit(1)

    for target in (repo, to_compare):
        if isinstance(target, SourceRepository):
            if not target.path.is_dir():
                diagnostic(Severity.ERROR, __name__, "The specified path is not a directory: %r", str(target.path))
                sys.exit(1)
            target.path = target.path.resolve()

    return repo, to_compare


def _approve_targets(
    settings: Settings,
    repo: SourceRepository | Dependency,
    to_compare: SourceRepository | Dependency | None,
) -> None:
    targets = [(repo, settings.target)]
    if to_compare is not None:
        targets.append((to_compare, settings.compare))
    try:
        require_trust(targets, trust=settings.trust)
    except TrustError as error:
        sys.stderr.write(f"{error}\n")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.stderr.write("\nTrust approval cancelled. No analysis was started.\n")
        sys.exit(130)


def _clear_cache(settings: Settings) -> None:
    if not settings.clear_cache:
        return
    db_path = Path(settings.database)
    if db_path.exists():
        db_path.unlink()
        diagnostic(Severity.INFO, __name__, "Cache cleared.")
    else:
        diagnostic(Severity.INFO, __name__, "No cache exists.")


def _list_resolvers(repo: SourceRepository | Dependency, target: str) -> None:
    # NOTE: This is so the user can pipe the output to another command.
    #       For example:
    #         $ it-depends "pip:numpy" --list | xargs -n1 > resolvers.txt
    #         $ cat resolvers.txt
    sys.stdout.flush()
    path = repo.path.absolute() if isinstance(repo, SourceRepository) else target
    sys.stderr.write(f"Available resolvers for {path}:\n")
    sys.stderr.flush()
    for name, classifier in sorted((c.name, c) for c in resolvers()):
        sys.stdout.write(name + " " * (12 - len(name)))
        sys.stdout.flush()
        available = classifier.is_available()
        if not available:
            sys.stderr.write(f"\tnot available: {available.reason}")
            sys.stderr.flush()
        elif isinstance(repo, SourceRepository) and not classifier.can_resolve_from_source(repo):
            sys.stderr.write("\tincompatible with this path")
            sys.stderr.flush()
        elif isinstance(repo, Dependency) and repo.source != classifier.name:
            sys.stderr.write("\tincompatible with this package specifier")
        else:
            sys.stderr.write("\tenabled")
            sys.stderr.flush()

        sys.stdout.write("\n")
        sys.stdout.flush()


def _get_output_writer(settings: Settings) -> Callable[[str], object]:
    if settings.output_file is None:
        return _write_stdout
    if not settings.force and settings.output_file.exists():
        diagnostic(
            Severity.ERROR,
            __name__,
            "%s already exists. Re-run with `--force` to overwrite the file.",
            settings.output_file,
        )
        sys.exit(1)
    return settings.output_file.write_text


def _write_output(
    settings: Settings,
    cache: DBPackageCache,
    package_list: PackageRepository,
    to_compare: SourceRepository | Dependency | None,
    *,
    output_write: Callable[[str], object],
) -> None:
    if to_compare is not None:
        to_compare_list = resolve(
            to_compare,
            cache=cache,
            depth_limit=settings.depth_limit,
            max_workers=settings.max_workers,
        )
        output_write(str(package_list.to_graph().distance_to(to_compare_list.to_graph(), normalize=settings.normalize)))
    elif settings.output_format == "dot":
        output_write(cache.to_dot(package_list.source_packages).source)
    elif settings.output_format == "html":
        output_write(graph_to_html(package_list, collapse_versions=not settings.all_versions))
        if isinstance(settings.output_file, Path) and settings.output_file.exists():
            webbrowser.open(str(settings.output_file.absolute()))
    elif settings.output_format == "json":
        output_write(json.dumps(package_list.to_obj(), sort_keys=True, indent=4))
    elif settings.output_format == "cyclonedx":
        sbom = None
        for p in package_list.source_packages:
            for bom in resolve_sbom(p, package_list, order_ascending=not settings.latest_resolution):
                sbom = bom if sbom is None else sbom | bom
                # only get the first resolution
                # TODO(@evandowning): Provide a means for enumerating all valid SBOMs # noqa: TD003, FIX002
                break
        if sbom is None:
            diagnostic(Severity.ERROR, __name__, "No SBOM found for %s", settings.target)
        else:
            output_write(cyclonedx_to_json(sbom.to_cyclonedx()))
    else:
        msg = f"TODO: Implement output format {settings.output_format}"
        raise NotImplementedError(msg)


def _enrich_packages(settings: Settings, packages: PackageRepository) -> PackageRepository:
    if settings.audit:
        packages = vulnerabilities(packages)
    if settings.check_maintenance:
        packages = check_maintenance_status(packages, stale_threshold_days=settings.stale_threshold)
    return packages


def _write_stdout(text: str) -> None:
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def main() -> None:
    settings = Settings()  # type: ignore[call-arg]
    try:
        reporter = make_reporter(settings.log_level)
    except ValueError as error:
        sys.stderr.write(f"{error}\n")
        sys.exit(2)
    with reporting_session(reporter), third_party_reporting(), CommandSession().running():
        try:
            _run(settings, reporter)
        except KeyboardInterrupt:
            diagnostic(Severity.WARNING, __name__, "Analysis interrupted")
            sys.exit(130)
        finally:
            reporter.stop()


def _run(settings: Settings, reporter: PlainReporter) -> None:
    # If max_workers isn't provided, use the number of CPUs.
    # If that fails, use 1.
    if settings.max_workers == -1:
        settings.max_workers = os.cpu_count() or 1

    diagnostic(Severity.DEBUG, __name__, "Starting it-depends with settings: %s", settings)

    repo, to_compare = _parse_targets(settings)

    if settings.list:
        _list_resolvers(repo, settings.target)
        return

    _approve_targets(settings, repo, to_compare)
    _clear_cache(settings)
    reporter.start()

    try:
        output_write = _get_output_writer(settings)

        def write_result(text: str) -> object:
            reporter.stop()
            return output_write(text)

        with DBPackageCache(settings.database) as cache:
            try:
                package_list = resolve(
                    repo,
                    cache=cache,
                    depth_limit=settings.depth_limit,
                    max_workers=settings.max_workers,
                )
            except ValueError as e:
                if not settings.clear_cache or settings.target.strip():
                    msg = f"{e!s}"
                    diagnostic(Severity.ERROR, __name__, msg, exception=True)
                sys.exit(1)
            if not package_list:
                diagnostic(
                    Severity.ERROR, __name__, "Try --list to check for available resolvers for %s", settings.target
                )

            package_list = _enrich_packages(settings, package_list)

            _write_output(settings, cache, package_list, to_compare, output_write=write_result)

    except OperationalError as e:
        msg = (
            f"Database error: {e!r}\n\nThis can occur if your database was created with an older version "
            f"of it-depends and was unable to be updated. If you remove {settings.database} or run "
            "`it-depends --clear-cache` and try again, the database will automatically be rebuilt from "
            "scratch."
        )
        diagnostic(Severity.ERROR, __name__, msg, exception=True)
        sys.exit(1)
    if settings.output_file is not None and settings.output_file.exists():
        diagnostic(Severity.INFO, __name__, "Output saved to %s", settings.output_file.absolute())

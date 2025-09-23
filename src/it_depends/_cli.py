"""Command-line interface for it-depends."""

from __future__ import annotations

import json
import logging
import os
import sys
import webbrowser
from pathlib import Path
from sqlite3 import OperationalError

from . import __version__ as it_depends_version
from .audit import vulnerabilities
from .config import Settings
from .db import DBPackageCache
from .dependencies import Dependency, SourceRepository, resolve, resolve_sbom, resolvers
from .html import graph_to_html
from .logger import setup_logger
from .sbom import cyclonedx_to_json

logger = logging.getLogger(__name__)


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


def main() -> None:  # noqa: C901, PLR0911, PLR0912, PLR0915
    settings = Settings()
    setup_logger(settings.log_level)

    # If max_workers isn't provided, use the number of CPUs.
    # If that fails, use 1.
    if settings.max_workers == -1:
        settings.max_workers = os.cpu_count() or 1

    logger.info("Starting it-depends with settings: %s", settings)

    if settings.version:
        logger.info("it-depends version %s", it_depends_version)
        return

    # Parse the target(s) -- either a path or a package name
    try:
        repo = parse_path_or_package_name(settings.target)

        if settings.compare != "":
            to_compare: SourceRepository | Dependency | None = parse_path_or_package_name(settings.compare)
        else:
            to_compare = None
    except ValueError as e:
        msg = str(e)
        logger.exception(msg)
        return

    # Clear the database cache
    if settings.clear_cache:
        db_path = Path(settings.database)
        if db_path.exists():
            db_path.unlink()

    # List the available resolvers
    if settings.list:
        path = repo.path.absolute() if isinstance(repo, SourceRepository) else settings.target
        logger.info("Available resolvers for %s:", path)
        for name, classifier in sorted((c.name, c) for c in resolvers()):
            logger.info(
                "%s...[!n]",
                name,
            )
            available = classifier.is_available()
            if not available:
                logger.info("not available: %s", available.reason)
            elif isinstance(repo, SourceRepository) and not classifier.can_resolve_from_source(repo):
                logger.info("incompatible with this path")
            elif isinstance(repo, Dependency) and repo.source != classifier.name:
                logger.info("incompatible with this package specifier")
            else:
                logger.info("enabled")
        return

    try:
        if settings.output_file is None:
            output_write = sys.stdout.write
        else:
            output_write = settings.output_file.write_text
            if not settings.force and settings.output_file.exists():
                logger.error("%s already exists!\nRe-run with `--force` to overwrite the file.\n", settings.output_file)
                return

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
                    msg = f"{e!s}\n"
                    logger.exception(msg)
                return
            if not package_list:
                logger.error("Try --list to check for available resolvers for %s\n", settings.target)

            # TODO(@evandowning): Should the cache be updated instead???? # noqa: TD003, FIX002
            if settings.audit:
                package_list = vulnerabilities(package_list)

            if to_compare is not None:
                to_compare_list = resolve(
                    to_compare,
                    cache=cache,
                    depth_limit=settings.depth_limit,
                    max_workers=settings.max_workers,
                )
                output_write(
                    str(package_list.to_graph().distance_to(to_compare_list.to_graph(), normalize=settings.normalize))
                )
            elif settings.output_format == "dot":
                output_write(cache.to_dot(package_list.source_packages).source)
            elif settings.output_format == "html":
                output_write(graph_to_html(package_list, collapse_versions=not settings.all_versions))
                if isinstance(settings.output_file, Path) and settings.output_file.exists():
                    webbrowser.open(str(settings.output_file.absolute()))
            elif settings.output_format == "json":
                output_write(json.dumps(package_list.to_obj(), indent=4))
            elif settings.output_format == "cyclonedx":
                sbom = None
                for p in package_list.source_packages:
                    for bom in resolve_sbom(p, package_list, order_ascending=not settings.latest_resolution):
                        sbom = bom if sbom is None else sbom | bom
                        # only get the first resolution
                        # TODO(@evandowning): Provide a means for enumerating all valid SBOMs # noqa: TD003, FIX002
                        break
                if sbom is not None:
                    output_write(cyclonedx_to_json(sbom.to_cyclonedx()))
            else:
                msg = f"TODO: Implement output format {settings.output_format}"
                raise NotImplementedError(msg)

    except OperationalError as e:
        msg = (
            f"Database error: {e!r}\n\nThis can occur if your database was created with an older version "
            f"of it-depends and was unable to be updated. If you remove {settings.database} or run "
            "`it-depends --clear-cache` and try again, the database will automatically be rebuilt from "
            "scratch."
        )
        logger.exception(msg)
        return
    finally:
        if settings.output_file is not None:
            logger.info("Output saved to %s\n", settings.output_file.absolute())

    return

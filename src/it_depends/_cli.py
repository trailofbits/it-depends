import argparse
import sys
from contextlib import contextmanager
import json
from pathlib import Path
from typing import Iterator, Optional, Sequence, TextIO, Union
import webbrowser
import logging

from sqlite3 import OperationalError

from .audit import vulnerabilities
from .db import DEFAULT_DB_PATH, DBPackageCache
from .dependencies import Dependency, resolvers, resolve, SourceRepository, resolve_sbom
from .html import graph_to_html
from .sbom import cyclonedx_to_json
from .logger import setup_logger
from .config import Settings

logger = logging.getLogger(__name__)


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


def main() -> None:
    settings = Settings()
    setup_logger(settings.log_level)

    logger.info("Starting it-depends with settings: %s", settings)

    try:
        repo = parse_path_or_package_name(settings.target)

        if settings.compare != "":
            to_compare: Optional[Union[SourceRepository, Dependency]] = parse_path_or_package_name(
                settings.compare
            )
        else:
            to_compare = None
    except ValueError as e:
        logger.error(str(e))
        return

    if settings.clear_cache:
        db_path = Path(settings.database)
        if db_path.exists():
            db_path.unlink()

    if settings.list:
        if isinstance(repo, SourceRepository):
            path = repo.path.absolute()
        else:
            path = settings.target
        logger.info(f"Available resolvers for {path}:\n")
        for name, classifier in sorted((c.name, c) for c in resolvers()):
            logger.info(name + " " * (12 - len(name)))
            available = classifier.is_available()
            if not available:
                logger.info(f"\tnot available: {available.reason}")
            elif isinstance(repo, SourceRepository) and not classifier.can_resolve_from_source(
                repo
            ):
                logger.info("\tincompatible with this path")
            elif isinstance(repo, Dependency) and repo.source != classifier.name:
                logger.info("\tincompatible with this package specifier")
            else:
                logger.info("\tenabled")

        return

    try:
        if settings.output_file is None:
            output_write = sys.stdout.write
        else:
            output_write = settings.output_file.write_text
            if not settings.force and settings.output_file.exists():
                logger.error(
                    f"{settings.output_file} already exists!\nRe-run with `--force` to overwrite the file.\n"
                )
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
                    logger.error(f"{e!s}\n")
                return
            if not package_list:
                logger.error(
                    f"Try --list to check for available resolvers for {settings.target}\n"
                )

            # TODO: Should the cache be updated instead????
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
                    str(
                        package_list.to_graph().distance_to(
                            to_compare_list.to_graph(), normalize=settings.normalize
                        )
                    )
                )
            elif settings.output_format == "dot":
                output_write(cache.to_dot(package_list.source_packages).source)
            elif settings.output_format == "html":
                output_write(
                    graph_to_html(package_list, collapse_versions=not settings.all_versions)
                )
                webbrowser.open(settings.output_file.absolute())
            elif settings.output_format == "json":
                output_write(json.dumps(package_list.to_obj(), indent=4))
            elif settings.output_format == "cyclonedx":
                sbom = None
                for p in package_list.source_packages:
                    for bom in resolve_sbom(p, package_list, order_ascending=not settings.latest_resolution):
                        if sbom is None:
                            sbom = bom
                        else:
                            sbom = sbom | bom
                        # only get the first resolution
                        # TODO: Provide a means for enumerating all valid SBOMs
                        break
                output_write(cyclonedx_to_json(sbom.to_cyclonedx()))
            else:
                raise NotImplementedError(f"TODO: Implement output format {args.output_format}")
    except OperationalError as e:
        logger.error(
            f"Database error: {e!r}\n\nThis can occur if your database was created with an older version "
            f"of it-depends and was unable to be updated. If you remove {settings.database} or run "
            "`it-depends --clear-cache` and try again, the database will automatically be rebuilt from "
            "scratch."
        )
        return
    finally:
        if settings.output_file is not None:
            logger.info(f"Output saved to {settings.output_file.absolute()}\n")

    return

"""Configuration settings for it-depends."""

from __future__ import annotations

from enum import Enum
from pathlib import Path  # noqa: TC003

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    CliImplicitFlag,
    SettingsConfigDict,
)

from .db import DEFAULT_DB_PATH


class OutputFormat(str, Enum):
    """Output formats for it-depends."""

    json = "json"
    dot = "dot"
    html = "html"
    cyclonedx = "cyclonedx"


class Settings(BaseSettings):
    """Settings for it-depends."""

    target: str = Field(
        default=".",
        description="""Directory or package name to analyze. If a package
            name is provided, it must be in the form of
            RESOLVER_NAME:PACKAGE_NAME[@OPTIONAL_VERSION], where RESOLVER_NAME
            is a resolver listed in `it-depends --list`.
            For example: `pip:numpy`, `apt:libc6@2.31`, or `npm:lodash@>=4.17.0`.""",
    )
    all_versions: CliImplicitFlag[bool] = Field(
        default=False,
        description="""For `--output-format html`, this option will emit all
        package versions that satisfy each dependency.""",
    )
    audit: CliImplicitFlag[bool] = Field(
        default=False,
        description="""Audit packages for known vulnerabilities using Google OSV.""",
    )
    database: Path = Field(
        default=DEFAULT_DB_PATH,
        description="""Alternative path to load/store the database, or
        ':memory:' to cache all results in memory rather than reading/writing
        to disk.""",
    )
    depth_limit: int = Field(
        default=-1,
        description="""Depth limit for recursively solving dependencies.""",
    )
    clear_cache: CliImplicitFlag[bool] = Field(
        default=False,
        description="""Clears the database specified by `--database` (equivalent
        to deleting the database file).""",
    )
    compare: str = Field(
        default="",
        description="""Compare path or package name to another package
            specified according to the same rules as `target`. This option
            will override the --output-format option and will instead output
            a floating point similarity metric. By default, the metric will be
            in the range [0, ∞), with zero meaning that the dependency graphs
            are identical. For a metric in the range [0, 1], see the
            `--normalize` option.""",
    )
    force: CliImplicitFlag[bool] = Field(
        default=False,
        description="""Force the overwrite of the output file if it already
        exists.""",
    )
    list: CliImplicitFlag[bool] = Field(
        default=False,
        description="""List available package resolvers.""",
    )
    log_level: str = Field(default="info", description="Log level")
    max_workers: int = Field(
        default=-1,
        description="""Maximum number of jobs to run concurrently. If not
            provided, the maximum number of logical CPUs will be used.""",
    )
    normalize: CliImplicitFlag[bool] = Field(
        default=False,
        description="""Used in conjunction with `--compare`, this will change
        the output metric to be in the range [0, 1] where 1 means the graphs
        are identical and 0 means the graphs are as different as possible.""",
    )
    output_file: Path | None = Field(
        default=None,
        description="""Output file path. If not provided, the output will be
        written to stdout.""",
    )
    output_format: OutputFormat = Field(
        default=OutputFormat.json,
        description="""Output format. Note that `cyclonedx` will output a
            single satisfying dependency resolution rather than the universe of
            all possible resolutions (see `--latest-resolution`).""",
    )
    latest_resolution: CliImplicitFlag[bool] = Field(
        default=False,
        description="""Output the latest resolution of the dependency graph.
        By default, the `cyclonedx` output format emits a single satisfying dependency
        resolution containing the oldest versions of all of the packages possible.
        Instead, this option returns the latest possible resolution.""",
    )
    version: CliImplicitFlag[bool] = Field(
        default=False,
        description="""Show the version of it-depends and exit.""",
    )

    model_config = SettingsConfigDict(
        cli_parse_args=True,
        nested_model_default_partial_update=True,
    )

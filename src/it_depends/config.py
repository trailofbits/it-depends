"""Configuration settings for it-depends."""

from __future__ import annotations

from enum import Enum
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Any

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    CliImplicitFlag,
    CliPositionalArg,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from it_depends.db import DEFAULT_DB_PATH
from it_depends.it_depends import APP_DIRS

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo


class _WithoutTrust(PydanticBaseSettingsSource):
    """Preserve a settings source while excluding execution authorization."""

    def __init__(self, source: PydanticBaseSettingsSource) -> None:
        super().__init__(source.settings_cls)
        self.source = source

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        """Delegate field lookup to the original source."""
        return self.source.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        """Read normal settings without accepting a stored or inherited trust flag."""
        values = self.source()
        values.pop("trust", None)
        return values


class OutputFormat(str, Enum):
    """Output formats for it-depends."""

    json = "json"
    dot = "dot"
    html = "html"
    cyclonedx = "cyclonedx"


class Settings(BaseSettings):
    """Settings for it-depends."""

    target: CliPositionalArg[str] = Field(
        description="""Directory or package name to analyze, or a saved JSON file with --explore. If a package
            name is provided, it must be in the form of
            RESOLVER_NAME:PACKAGE_NAME[@OPTIONAL_VERSION], where RESOLVER_NAME
            is a resolver listed in `it-depends --list`.
            For example: `pip:numpy`, `apt:libc6@2.31`, or `npm:lodash@>=4.17.0`.""",
    )
    all_versions: CliImplicitFlag[bool] = Field(
        alias="all-versions",
        default=False,
        description="""For `--output-format html`, this option will emit all
        package versions that satisfy each dependency.""",
    )
    explore: CliImplicitFlag[bool] = Field(
        default=False,
        description="""Explore a saved native it-depends JSON or CycloneDX JSON file offline.
        Requires interactive stdin, stdout, and stderr. Does not analyze or execute the target.""",
    )
    audit: CliImplicitFlag[bool] = Field(
        default=False,
        description="""Audit packages for known vulnerabilities using Google OSV.""",
    )
    trust: CliImplicitFlag[bool] = Field(
        default=False,
        description=f"""Trust both requested targets and their dependencies for this run only.
        Analysis may execute their code with your user permissions. Does not save approval.
        Saved interactive approvals are in {APP_DIRS.user_config_path / "trust.json"}.
        To revoke an approval, remove its entry from that file; delete the file to revoke all approvals.
        --clear-cache does not revoke approvals.""",
    )
    check_maintenance: CliImplicitFlag[bool] = Field(
        alias="check-maintenance",
        default=False,
        description="""Check maintenance status of GitHub-hosted packages.
        Queries GitHub API for last commit date and flags stale packages.""",
    )
    stale_threshold: int = Field(
        alias="stale-threshold",
        default=365,
        ge=0,
        description="""Days since last commit to consider a package stale.
        Default: 365 (1 year). Requires --check-maintenance.""",
    )
    database: Path = Field(
        default=DEFAULT_DB_PATH,
        description="""Alternative path to load/store the database, or
        ':memory:' to cache all results in memory rather than reading/writing
        to disk.""",
    )
    depth_limit: int = Field(
        alias="depth-limit",
        default=-1,
        description="""Depth limit for recursively solving dependencies.""",
    )
    clear_cache: CliImplicitFlag[bool] = Field(
        alias="clear-cache",
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
    log_level: str = Field(alias="log-level", default="info", description="Log level")
    max_workers: int = Field(
        alias="max-workers",
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
        alias="output-file",
        default=None,
        description="""Output file path. If not provided, the output will be
        written to stdout.""",
    )
    output_format: OutputFormat = Field(
        alias="output-format",
        default=OutputFormat.json,
        description="""Output format. Note that `cyclonedx` will output a
            single satisfying dependency resolution rather than the universe of
            all possible resolutions (see `--latest-resolution`).""",
    )
    latest_resolution: CliImplicitFlag[bool] = Field(
        alias="latest-resolution",
        default=False,
        description="""Output the latest resolution of the dependency graph.
        By default, the `cyclonedx` output format emits a single satisfying dependency
        resolution containing the oldest versions of all of the packages possible.
        Instead, this option returns the latest possible resolution.""",
    )

    model_config = SettingsConfigDict(
        cli_parse_args=True,
        nested_model_default_partial_update=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],  # noqa: ARG003 - Required Pydantic hook parameter.
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Require CLI or explicit constructor authorization for the trust flag."""
        return (
            init_settings,
            _WithoutTrust(env_settings),
            _WithoutTrust(dotenv_settings),
            _WithoutTrust(file_secret_settings),
        )

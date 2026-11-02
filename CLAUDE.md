# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

It-Depends is a software dependency analyzer that builds dependency graphs and Software Bills of Materials (SBOMs) for packages across multiple ecosystems (Python/pip, JavaScript/npm, Rust/cargo, Go, C/C++ via cmake/autotools, and Ubuntu/apt). It resolves *all possible* dependency versions rather than a single feasible resolution.

## Common Commands

```bash
# Setup development environment
make dev
source .venv/bin/activate

# Run the tool
uv run it-depends .                    # Analyze current directory
uv run it-depends pip:numpy            # Analyze a pip package
uv run it-depends --audit pip:numpy    # Include vulnerability audit

# Linting and formatting
make format                            # Auto-fix formatting with ruff
make lint                              # Check formatting, linting, types, docstrings

# Testing
make test                              # Run unit tests with coverage
make test TESTS=test_resolver          # Run specific test pattern
make integration                       # Run integration tests (slow, requires docker)
```

## Architecture

### Resolver System

The core abstraction is `DependencyResolver` (src/it_depends/resolver.py), an abstract base class that ecosystem-specific resolvers inherit from. Each resolver is a singleton that auto-registers when its module is imported.

**Auto-registration flow:**
1. `__init__.py` uses `pkgutil.iter_modules` to import every module in the package at load time
2. Each resolver module defines a `DependencyResolver` subclass with `name` and `description` class attrs
3. `__init_subclass__` fires at class definition, validates the attrs exist, and clears the `resolvers()` lru_cache
4. `resolvers()` returns all registered instances via `DependencyResolver.__subclasses__()`

**Adding a new resolver:** Subclass `DependencyResolver`, set `name` and `description` class attrs, implement three abstract methods (`resolve`, `can_resolve_from_source`, `resolve_from_source`), and place the file in `src/it_depends/`. The auto-import handles registration.

Built-in resolvers:
- `PipResolver` (pip.py) - Python packages via johnnydep
- `NPMResolver` (npm.py) - JavaScript packages via npm CLI
- `CargoResolver` (cargo.py) - Rust packages via cargo metadata
- `GoResolver` (go.py) - Go modules via go.mod parsing
- `CMakeResolver` (cmake.py), `AutotoolsResolver` (autotools.py) - C/C++ build systems
- `UbuntuResolver` (ubuntu/resolver.py) - Ubuntu packages via apt-file (runs in Docker); this is a subpackage with its own `apt.py` and `docker.py`

### CLI

The CLI entry point is `_cli.py:main()`. It uses **pydantic-settings** (`Settings` class in `config.py`) with `cli_parse_args=True` to parse `sys.argv` directly — there is no argparse. To add/modify CLI arguments, edit the `Settings` class in `config.py`.

Package spec format: `RESOLVER:PACKAGE[@VERSION]` (e.g., `pip:numpy`, `npm:lodash@>=4.17.0`)

### Key Modules

- `models.py` - Core data classes: `Package`, `SourcePackage`, `Dependency`, `Vulnerability`
- `resolver.py` - Base `DependencyResolver` class, `PackageSet`, `PartialResolution`
- `resolution.py` - Main resolution logic (`resolve()`, `resolve_sbom()`)
- `db.py` - `DBPackageCache` (SQLite-backed), used by the CLI
- `cache.py` - `PackageCache`, `InMemoryPackageCache` (in-memory variant)
- `graph.py` - `DependencyGraph` built on networkx
- `repository.py` - `SourceRepository` for local source code
- `native.py` - Native library dependency resolution via Docker
- `audit.py` - Vulnerability checking against OSV database
- `sbom.py` - CycloneDX SBOM generation
- `dependencies.py` - Facade re-exporting core APIs for backward compatibility

### Key Patterns

- Version handling uses `semantic_version` library; some resolvers define custom spec types (e.g., `CargoSpec`, `GoSpec`)
- Integration tests are marked with `@pytest.mark.integration` and skipped by default; use `pytest --runintegration` (defined in `test/conftest.py`)
- Tests live in `test/` with expected outputs in `test/expected_output/`

## Code Style

- Line length: 120 characters
- Ruff handles all linting with `select = ["ALL"]`
- Google-style docstrings required (enforced by interrogate at 95% coverage)
- Type hints required (enforced by mypy)

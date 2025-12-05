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

# Individual lint tools
uv run ruff format --check             # Check formatting
uv run ruff check                      # Lint
uv run mypy                            # Type check
uv run interrogate -c pyproject.toml . # Check docstring coverage (90% minimum)
```

## Architecture

### Resolver System

The core abstraction is `DependencyResolver` (src/it_depends/resolver.py), an abstract base class that ecosystem-specific resolvers inherit from. Each resolver is a singleton that auto-registers when its module is imported.

Resolvers must implement:
- `resolve(dependency)` - Yield packages satisfying a dependency
- `can_resolve_from_source(repo)` - Check if resolver handles a source repo
- `resolve_from_source(repo)` - Extract dependencies from source code

Built-in resolvers (each in their own module):
- `PipResolver` - Python packages via johnnydep
- `NPMResolver` - JavaScript packages via npm CLI
- `CargoResolver` - Rust packages via cargo metadata
- `GoResolver` - Go modules via go.mod parsing
- `CMakeResolver`, `AutotoolsResolver` - C/C++ build systems
- `UbuntuResolver` - Ubuntu packages via apt-file (runs in Docker)

### Module Structure

- `dependencies.py` - Facade module re-exporting core APIs for backward compatibility
- `models.py` - Core data classes: `Package`, `SourcePackage`, `Dependency`, `Vulnerability`
- `resolver.py` - Base `DependencyResolver` class, `PackageSet`, `PartialResolution`
- `resolution.py` - Main resolution logic (`resolve()`, `resolve_sbom()`)
- `cache.py` - `PackageCache`, `InMemoryPackageCache` for caching resolved packages
- `graph.py` - `DependencyGraph` built on networkx
- `repository.py` - `SourceRepository` for local source code
- `native.py` - Native library dependency resolution via Docker
- `audit.py` - Vulnerability checking against OSV database
- `sbom.py` - CycloneDX SBOM generation

### Key Patterns

- Resolvers auto-register via `__init_subclass__` and are accessed via `resolvers()` or `resolver_by_name()`
- All modules in `src/it_depends/` are auto-imported in `__init__.py` to trigger resolver registration
- Version handling uses `semantic_version` library; some resolvers define custom spec types (e.g., `CargoSpec`, `GoSpec`)
- Integration tests are marked with `@pytest.mark.integration` and skipped by default

### Testing

Tests live in `test/` with expected outputs in `test/expected_output/`. Use `--runintegration` flag for integration tests which require external tools (npm, cargo, go, etc.) and Docker.

## Code Style

- Line length: 120 characters
- Ruff handles all linting with `select = ["ALL"]`
- Google-style docstrings required (enforced by interrogate at 90% coverage)
- Type hints required (enforced by mypy)

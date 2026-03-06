# It-Depends

[![Unit tests](https://github.com/trailofbits/it-depends/actions/workflows/tests.yml/badge.svg)](https://github.com/trailofbits/it-depends/actions/workflows/tests.yml)
[![Integration tests](https://github.com/trailofbits/it-depends/actions/workflows/integration.yml/badge.svg)](https://github.com/trailofbits/it-depends/actions/workflows/integration.yml)
[![PyPI version](https://badge.fury.io/py/it-depends.svg)](https://badge.fury.io/py/it-depends)
[![Slack Status](https://slack.empirehacking.nyc/badge.svg)](https://slack.empirehacking.nyc)

It-Depends is a tool to automatically build a dependency graph and Software Bill of Materials (SBOM) for packages and arbitrary source code repositories. It supports Go, JavaScript, Rust, Python, C/C++ (cmake and autotools), and Ubuntu packages.

What makes it different from [similar tools](SIMILAR.md):

* Resolves _all possible_ dependency versions, not just a single feasible resolution
* C/C++ support via cmake and autotools without building the project
* Automated native library dependency mapping via dynamic analysis (_e.g._, `pytz` depends on `libtinfo.so.6`)
* Vulnerability scanning against the [OSV database](https://osv.dev/)
* Dependency similarity comparison between packages

## Installation

```shell
pip3 install it-depends
```

Ecosystem-specific tools must be installed separately: `npm` for JavaScript, `cargo` for Rust, `pip` for Python, `autotools`/`cmake` for C/C++. Native dependency resolution and Ubuntu package analysis require a Docker-compatible container runtime with an accessible socket (_e.g._, Docker Desktop, Podman, or Colima).

## Usage

```shell
it-depends .                            # Analyze current directory
it-depends /path/to/project             # Analyze a source repository
it-depends "pip:numpy"                  # Analyze a pip package
it-depends "npm:lodash@>=4.17.0"        # Specify a version constraint
it-depends --audit pip:numpy            # Include vulnerability audit
it-depends . --list                     # List compatible resolvers
it-depends --output-format dot .        # Output as Graphviz/Dot
it-depends --depth-limit 1 pip:numpy    # Only direct dependencies
```

## Development

```shell
git clone https://github.com/trailofbits/it-depends
cd it-depends
make dev
uv run it-depends --help
make format lint
```

## Acknowledgements

This research was developed by [Trail of Bits](https://www.trailofbits.com/) based upon work supported by DARPA under Contract No. HR001120C0084 (Distribution Statement A, Approved for Public Release: Distribution Unlimited). Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Government or DARPA.

[Evan Sultanik](https://github.com/ESultanik) and [Evan Downing](https://github.com/evandowning) are the active maintainers. [Felipe Manzano](https://github.com/feliam), [Alessandro Gario](https://github.com/alessandrogario), [Eric Kilmer](https://github.com/ekilmer), [Alexander Remie](https://github.com/rmi7), and [Henrik Brodin](https://github.com/hbrodin) all made significant contributions to the tool's inception and development.

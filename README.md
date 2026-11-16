# It-Depends

[![Unit tests](https://github.com/trailofbits/it-depends/actions/workflows/tests.yml/badge.svg)](https://github.com/trailofbits/it-depends/actions/workflows/tests.yml)
[![Integration tests](https://github.com/trailofbits/it-depends/actions/workflows/integration.yml/badge.svg)](https://github.com/trailofbits/it-depends/actions/workflows/integration.yml)
[![PyPI version](https://badge.fury.io/py/it-depends.svg)](https://badge.fury.io/py/it-depends)
[![Slack Status](https://slack.empirehacking.nyc/badge.svg)](https://slack.empirehacking.nyc)

It-Depends is a tool to automatically build a dependency graph and Software Bill of Materials (SBOM) for packages and arbitrary source code repositories. It supports Go, JavaScript, Rust, Python, C/C++ (cmake and autotools), and Ubuntu packages.

What makes it different from [similar tools](doc/SIMILAR.md):

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
it-depends .                        # Analyze current directory
it-depends . --list                 # List compatible resolvers
it-depends /path/to/project         # Analyze a different repository

it-depends "pip:numpy"              # Analyze a pip package
it-depends "ubuntu:libc6@2.35"      # Analyze a Ubuntu package
it-depends "npm:lodash@>=4.17.0"    # Specify a version constraint

it-depends --audit "pip:numpy"                          # Include vulnerability audit
it-depends --depth-limit 1 "pip:scikit-learn"           # Only direct dependencies
it-depends --output-format dot --output-file file.dot . # Output as Graphviz/Dot
```

### Checking package maintenance

```shell
it-depends pip:requests --check-maintenance
it-depends npm:lodash --check-maintenance --stale-threshold 180
```

### Trusting analysis targets

It-Depends dynamically resolves dependencies by invoking ecosystem tools. Analyzing a
directory or registry package may execute code from that target and its dependencies with
your user permissions. For example, CMake configuration, Autotools macros, and Python build
hooks can execute commands during analysis, even when no full build is required. Internal
Docker usage does not sandbox the entire analysis. Run untrusted inputs in an appropriately
isolated environment.

For each unfamiliar target, the CLI offers **Trust permanently**, **Trust for this run only**,
and **Cancel (exit)**. Cancel is the default. Only permanent approvals are saved. When you
use `--compare`, you must approve both targets before either analysis starts. Approval
includes the target's dependency tree; it does not save independent approvals for those
dependencies.

Directory approval applies to the exact absolute path after resolving symlinks. It persists
across content changes, but does not apply to parent directories, child directories, or a
checkout moved to a different path.

Package approval includes the resolver and package name. Approving `pip:numpy` trusts all
versions, including future releases. Approving `pip:numpy@=1.0.0` remembers only that supplied
constraint. Other version constraints need separate approval, even if they overlap. Version
constraints retain their resolver's meaning: `pip:numpy@1.0` selects the 1.0.x range, while
`pip:numpy@=1.0.0` pins one version.

Interactive approval requires terminal input and output. For headless execution or redirected
output, explicitly authorize the current run:

```shell
it-depends --trust /path/to/project
it-depends --trust pip:numpy --compare npm:lodash
```

`--trust` approves both requested targets and their dependencies for that run only. It does
not read or change remembered approvals, and environment variables cannot enable it.
`--help` and `--list` do not require approval.

Approvals are stored in `trust.json` under the per-user configuration directory returned by
`it_depends.it_depends.APP_DIRS.user_config_path`, using platformdirs' operating system
conventions. The store is separate from the dependency cache, so `--clear-cache` preserves
approvals. To revoke approval, remove the corresponding entry, or delete the file to revoke
all approvals. Its format is:

```json
{
  "directories": ["/absolute/path/to/project"],
  "packages": ["pip:numpy", "npm:@scope/pkg@1.0.0"]
}
```

The Python API does not prompt or consult the CLI trust store. If you call `resolve()` or
individual resolvers, you are responsible for trusting their inputs and dependencies.

## Development

```shell
git clone https://github.com/trailofbits/it-depends
cd it-depends
make sync
uv run it-depends --help
make format lint integration
```

## Acknowledgements

This research was developed by [Trail of Bits](https://www.trailofbits.com/) based upon work supported by DARPA under Contract No. HR001120C0084 (Distribution Statement A, Approved for Public Release: Distribution Unlimited). Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Government or DARPA.

[Evan Sultanik](https://github.com/ESultanik) and [Evan Downing](https://github.com/evandowning) are the active maintainers. [Felipe Manzano](https://github.com/feliam), [Alessandro Gario](https://github.com/alessandrogario), [Eric Kilmer](https://github.com/ekilmer), [Alexander Remie](https://github.com/rmi7), and [Henrik Brodin](https://github.com/hbrodin) all made significant contributions to the tool's inception and development.

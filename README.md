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

### Output and diagnostics

It-Depends writes the selected result to standard output and diagnostics to standard error.
Use `--output-file report.json` to save the result instead. `--log-level` controls diagnostics
without suppressing results; `--log-level debug` includes exception tracebacks.

Python API calls do not configure global logging. To receive application events, use
`it_depends.reporting.reporting_session(callback)` around your analysis call. The callback
receives diagnostic and progress events, including events from resolver worker threads.

When stdin, stdout, and stderr are capable terminals, analysis displays a live Rich dashboard
with discovery counts, active tasks, and bounded subtool output panels. Redirecting any stream
disables the dashboard. Analysis exits automatically when finished; warnings and a summary remain
in scrollback. `NO_COLOR` disables colors, and dumb terminals receive plain diagnostics.

Ctrl+C cancels queued work and owned subprocesses. In a terminal, you can choose to export partial
results; interrupted runs exit with status 130. A second Ctrl+C forces exit. In-process library
calls may need to finish after the first interrupt. Python API calls propagate interruption
without prompting.

### Exploring saved results

Save a result, then open it in the offline explorer:

```shell
it-depends --output-file dependencies.json pip:requests
it-depends --explore dependencies.json
```

The explorer reads native it-depends JSON, including all candidate versions and dependency
constraints, and the component/dependency relationships in CycloneDX JSON 1.2 through 1.7.
It preserves saved component references and version text. Exploration uses only the saved data;
missing components and constraints that cannot be matched remain visible.

Use Up/Down to navigate, Right or Space to expand, Left to collapse, and Enter to follow a
reference. Shared dependencies and cycles link to their package entries. Use `/` to search
package names, versions, references, or package URLs, Enter to return to the tree, Tab to switch
panes, and `q` or Escape to close the explorer. Details include incoming dependencies and any
saved vulnerability or maintenance information.

The explorer requires terminal stdin, stdout, and stderr. Analysis exits normally without opening
it. When you explicitly export JSON or CycloneDX, the completion message includes a command to
reopen that file. Stdout-only runs do not save an additional copy.

### Checking package maintenance

```shell
it-depends pip:requests --check-maintenance
it-depends npm:lodash --check-maintenance --stale-threshold 180
```

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

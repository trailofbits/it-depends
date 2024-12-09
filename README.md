
# It-Depends
[![PyPI version](https://badge.fury.io/py/it-depends.svg)](https://badge.fury.io/py/it-depends)
[![Tests](https://github.com/trailofbits/it-depends/workflows/tests/badge.svg?branch=master)](https://github.com/trailofbits/it-depends/actions)
[![Slack Status](https://slack.empirehacking.nyc/badge.svg)](https://slack.empirehacking.nyc)

It-Depends is a tool to automatically build a dependency graph and Software Bill of Materials (SBOM) for packages and arbitrary source code repositories. You can use it to enumerate all third party dependencies for a software package, map those dependencies to known security vulnerabilities, as well as compare the similarity between two packages based on their dependencies.

To the best of our knowledge, It-Depends is the only such tool with the following features:
  * Support for C/C++ projects (both autootools and cmake)
  * Automated resolution of native library dependencies partially based on dynamic analysis (_e.g._, the Python package `pytz` depends on the native library `libtinfo.so.6`)
  * Enumeration of _all possible_ dependency resolutions, not just a _single feasible_ resolution
  * A comparison metric for the similarity between two packages based on their dependency graph

## Features ⭐
 * Supports Go, JavaScript, Rust, Python, and C/C++ projects.
 * Accepts source code repositories or package specifications like `pip:it-depends`
 * Extracts dependencies of cmake/autotool repostories without building it
 * Finds native dependencies for high level languages like Python or JavaScript
 * Provides visualization based on vis.js or dot
 * Matches dependencies and CVEs
 * Export Software Bills of Materials (SBOMs)
   * Machine-intelligible JSON output
   * Support for the SPDX standard is [in active development](https://github.com/trailofbits/it-depends/tree/dev/spdx)

### Can It-Depends Do It? It Depends. 🍋
 * It-Depends does not detect vendored or copy/pasted dependencies
 * Results from build systems like autotools and cmake that entail arbitrary computation at install time are 
   best-effort
 * Resolution of native dependencies is best-effort
   * Some native dependencies are resolved through dynamic analysis
   * Native dependencies are inferred by cross-referencing file requirements against paths provided by the Ubuntu 
     package repository; dependencies may be different across other Linux distributions or Ubuntu versions
 * It-Depends attempts to resolve *all* possible package versions that satisfy a dependency
   * It-Depends *does not* find a single satisfying package resolution
   * The list of resolved packages is intended to be a superset of the packages required by the installation of
     a package on any system
   * The `--audit` feature may discover vulnerabilities in upstream dependencies that are either not exploitable in the 
     target package or are in a package version that cannot exist in any valid dependency resolution of the target
     package
 * It-Depends caches data that it expects to be immutable in a local database
   * If a package is ever deleted or yanked from a package repository after it was already cached, It-Depends will 
     continue to use the cached data unless the cache is cleared with `--clear-cache`


## Quickstart 🚀
```commandline
$ pip3 install it-depends
```

### Running it 🏃
Run `it-depends` in the root of the source repository you would like to analyze:
```console
$ cd /path/to/project
$ it-depends
```
or alternatively point it to the path directly:
```console
$ it-depends /path/to/project
```
or alternatively specify a package from a public package repository:
```console
$ it-depends pip:numpy
$ it-depends apt:libc6@2.31
$ it-depends npm:lodash@>=4.17.0
```

It-Depends will output the full dependency hierarchy in JSON format. Additional output formats such
as Graphviz/Dot are available via the `--output-format` option.

It-Depends can automatically try to match packages against the [OSV vulnerability database](https://osv.dev/) with the
`--audit` option. This is a best-effort matching as it is based on package names, which might not always consistent.
Any discovered vulnerabilities are added to the JSON output.

It-Depends attempts to parallelize as much of its effort as possible. To limit the maximum number of parallel tasks, use
the `--max-workers` option.

By default, It-Depends recursively resolves all packages' dependencies to construct a complete dependency graph. The
depth of the recursion can be limited using the `--depth-limit` option. For example,
```console
$ it-depends pip:graphtage --depth-limit 1
```
will only enumerate the direct dependencies of Graphtage.

### Examples 🧑‍🏫

Here is an example of running It-Depends on its own source repository:
![](https://gist.githubusercontent.com/feliam/e906ce723333b2b55237a71c4028559e/raw/e60f46c35b215a73a37a1d1ce3bb43eaead76af4/it-depends-demo.svg?sanitize=1)

This is the resulting [json](https://gist.github.com/feliam/2bdec76f7aa50602869059bfa14df156)
with all the discovered dependencies.
This is the resulting [Graphviz dot file](https://gist.github.com/feliam/275951f5788c23a477bc7cf758a32cc2)
producing this
![dependency graph](https://user-images.githubusercontent.com/1017522/116887041-33903b80-ac00-11eb-9288-f3d286231e47.png)

This is the resulting dependency graph:
![dependency graph](https://user-images.githubusercontent.com/1017522/126380710-0bf4fd66-0d2f-4cb1-a0ff-96fe715c4981.png)

### It-Depends’ Dependencies 🎭

JavaScript requires `npm`\
Rust requires `cargo`\
Python requires `pip`\
C/C++ requires `autotools` and/or `cmake`\
Several native dependencies are resolved using Ubuntu’s file to path database `apt-file`, but this is seamlessly
handled through an Ubuntu `docker` container on other distributions and operating systems\
Currently `docker` is used to resolve native dependencies

## Development 👷
```commandline
$ git clone https://github.com/trailofbits/it-depends
$ cd it-depends
$ python3 -m venv venv  # Optional virtualenv
$ ./venv/bin/activate   # Optional virtualenv
$ pip3 install -e '.[dev]'
$ git config core.hooksPath ./hooks  # Optionally enable git commit hooks for linting
```

## License and Acknowledgements 📃️

This research was developed by [Trail of Bits](https://www.trailofbits.com/) based upon work supported by DARPA under Contract No. HR001120C0084 (Distribution Statement **A**, Approved for Public Release: Distribution Unlimited).  Any opinions, findings and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the United States Government or DARPA.

[Felipe Manzano](https://github.com/feliam) and [Evan Sultanik](https://github.com/ESultanik) are
the active maintainers, but [Alessandro Gario](https://github.com/alessandrogario),
[Eric Kilmer](https://github.com/ekilmer), [Alexander Remie](https://github.com/rmi7), and [Henrik Brodin](https://github.com/hbrodin) all made significant
contributions to the tool’s inception and development.

It-Depends is licensed under the [GNU Lesser General Public License v3.0](LICENSE). [Contact us](mailto:opensource@trailofbits.com) if you’re looking for an exception to the terms.

© 2021, Trail of Bits.

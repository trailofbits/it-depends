# Similar Tools

It-Depends is a dependency analyzer that builds complete dependency graphs and SBOMs. Unlike most tools in this space, it can resolve *all possible* dependency versions (not just a single resolution), supports C/C++ projects via cmake/autotools, and maps native library dependencies through dynamic (runtime) analysis.

## Comparison

| Tool | Type | All-version resolution | C/C++ support | Native lib mapping | SBOM generation | Vuln scanning | Open source |
|------|------|:---:|:---:|:---:|:---:|:---:|:---:|
| **It-Depends** | Dependency analyzer | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| [Syft](https://github.com/anchore/syft) | SBOM generator | ❌ | ✅ | ❌ | ✅ | ❌ | ✅ |
| [cdxgen](https://github.com/CycloneDX/cdxgen) | SBOM generator | ❌ | ✅ | ❌ | ✅ | ❌ | ✅ |
| [Trivy](https://github.com/aquasecurity/trivy) | SBOM generator / scanner | ❌ | 🟨 | ❌ | ✅ | ✅ | ✅ |
| [Grype](https://github.com/anchore/grype) | Vuln scanner | ❌ | 🟨 | ❌ | ❌ | ✅ | ✅ |
| [OSV-Scanner](https://github.com/google/osv-scanner) | Vuln scanner | ❌ | ✅ | ❌ | ❌ | ✅ | ✅ |
| [deps.dev](https://deps.dev/) | Dependency-graph API | ❌ | ❌ | ❌ | ❌ | ✅ | 🟨 |
| [OWASP Dependency-Track](https://owasp.org/www-project-dependency-track/) | SBOM monitoring | ❌ | ❌ | ❌ | 🟨 | ✅ | ✅ |
| [ORT](https://github.com/oss-review-toolkit/ort) | SCA / compliance | ❌ | 🟨 | ❌ | ✅ | ✅ | ✅ |
| [Snyk](https://snyk.io/) | SCA platform | ❌ | ✅ | ❌ | ✅ | ✅ | ❌ |
| [FOSSA](https://fossa.com/) | SCA platform | ❌ | ✅ | 🟨 | ✅ | ✅ | 🟨 |
| [Mend](https://www.mend.io/) | SCA platform | ❌ | 🟨 | ❌ | ✅ | ✅ | ❌ |
| [Black Duck](https://www.blackduck.com/) | SCA platform | ❌ | ✅ | 🟨 | ✅ | ✅ | ❌ |
| [OWASP Dep-Check](https://github.com/dependency-check/DependencyCheck) | SCA / vuln scanner | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| [Endor Labs](https://www.endorlabs.com/) | SCA (reachability) | ❌ | ✅ | ❌ | ✅ | ✅ | ❌ |
| [Semgrep Supply Chain](https://semgrep.dev/products/semgrep-supply-chain/) | SCA (reachability) | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ |
| [Socket](https://socket.dev/) | SCA (reachability) | ❌ | 🟨 | ❌ | ✅ | ✅ | 🟨 |
| [Dependabot](https://github.com/dependabot) | Dependency updater | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| [Renovate](https://github.com/renovatebot/renovate) | Dependency updater | ❌ | 🟨 | ❌ | ❌ | ✅ | ✅ |

> *"C/C++ support" means the tool detects C/C++ dependencies via at least one mechanism — e.g. a manifest format (conan, vcpkg), the system package manager (apt, rpm), or source-file hashing. It-Depends additionally resolves dependencies directly from cmake and autotools sources. 🟨 indicates partial support (e.g. Conan-only, or inherited via another tool).*
>
> *"Native lib mapping" means resolving native shared-library (`.so`) dependencies. It-Depends does this through dynamic runtime tracing (strace inside Docker), observing libraries that are actually loaded — including `dlopen`'d ones. 🟨 marks tools (FOSSA, Black Duck) that map native libraries only via static `ldd` load-time resolution.*
>
> *"SBOM generation" means producing an SBOM. Ingesting or scanning an existing SBOM does not count — hence OSV-Scanner's ❌. Dependency-Track is 🟨: it ingests SBOMs and can re-export an enriched CycloneDX/VEX document, but cannot generate one from source.*
>
> *🟨 under "Open source" (deps.dev, FOSSA, Socket) marks a proprietary or hosted core paired with an open component — deps.dev's API definitions and CC-BY dataset, FOSSA's `fossa-cli`, and Socket's CLI — while the analysis engine itself remains closed.*

## Categories

### SBOM Generators

- **[Syft](https://github.com/anchore/syft)** -- Generates SBOMs from container images and filesystems. Supports CycloneDX and SPDX formats. Focused on cataloging what's installed rather than resolving dependency trees.
- **[cdxgen](https://github.com/CycloneDX/cdxgen)** (OWASP) -- Polyglot CycloneDX SBOM generator covering 20+ language ecosystems, including C/C++ via Conan and source/build analysis. Focused on SBOM production; delegates vulnerability analysis to companion tools such as OWASP dep-scan or Dependency-Track.
- **[Trivy](https://github.com/aquasecurity/trivy)** -- All-in-one security scanner for containers, filesystems, and git repositories. Generates SBOMs and scans for vulnerabilities, misconfigurations, and secrets.

### SBOM Management / Monitoring

- **[OWASP Dependency-Track](https://owasp.org/www-project-dependency-track/)** -- Server-side platform that ingests SBOMs and continuously re-checks their components against vulnerability feeds (NVD, OSV, GitHub Advisories, OSS Index) across a project portfolio. It analyzes SBOMs rather than generating them from source, though it can re-export an enriched CycloneDX BOM with VEX.

### Dependency Analysis / SCA

- **[ORT](https://github.com/oss-review-toolkit/ort)** (OSS Review Toolkit) -- Comprehensive open-source compliance toolchain. Analyzes dependencies, scans for licenses, and generates reports. Broad ecosystem support but resolves a single dependency tree.
- **[Snyk](https://snyk.io/)** -- Commercial SCA platform that monitors dependencies for vulnerabilities. Integrates with CI/CD pipelines and provides fix suggestions. Closed-source core.
- **[FOSSA](https://fossa.com/)** -- Commercial SCA with a license-compliance heritage. Has GA C/C++ support and can map native shared-library dependencies by running `ldd` on built binaries (`--detect-dynamic`) — the closest any listed tool comes to It-Depends' native-lib mapping, though it uses static load-time resolution rather than runtime tracing. The `fossa-cli` is open source; the backend is proprietary.
- **[Mend](https://www.mend.io/)** -- Commercial SCA/AppSec platform (formerly WhiteSource) with static call-graph reachability and automated remediation. C/C++ support is limited to Conan manifests. Proprietary SaaS.
- **[Black Duck](https://www.blackduck.com/)** -- Commercial SCA (formerly Synopsys) that inventories components across source, binaries, and containers via package managers, signature/snippet matching, and binary analysis. Strong multi-mechanism C/C++ support; maps `.so` dependencies via `ldd` within its C/C++ flow.
- **[deps.dev](https://deps.dev/)** (Open Source Insights) -- Google-hosted service exposing precomputed dependency graphs, license data, and advisories through an API and a BigQuery dataset. Conceptually the closest cousin to It-Depends' cross-ecosystem graph, but it serves a single resolved graph per package version, has no C/C++ data, and runs as a hosted service (the API definitions and dataset are open; the analysis engine is not).
- **[OWASP Dependency-Check](https://github.com/dependency-check/DependencyCheck)** -- Identifies known vulnerabilities in project dependencies by cross-referencing against the NVD. Primarily Java-focused but supports other ecosystems.

### Reachability-based SCA

A newer category uses call-graph or dataflow reachability analysis to determine whether a vulnerable function is actually reachable from first-party code, suppressing noise from unreachable CVEs. These tools resolve a single locked dependency graph (not all versions) and layer reachability on top.

- **[Endor Labs](https://www.endorlabs.com/)** -- AppSec platform built around static call-graph reachability, deprioritizing unreachable vulnerable functions. First-class C/C++ support via source-file fingerprinting. Proprietary.
- **[Semgrep Supply Chain](https://semgrep.dev/products/semgrep-supply-chain/)** -- Lockfile-driven SCA with static dataflow reachability, sold as a paid add-on to the Semgrep platform. The open-source Semgrep engine is the SAST scanner, not this SCA product, which does not cover C/C++.
- **[Socket](https://socket.dev/)** -- Supply-chain scanner combining behavioral malware detection with CVE scanning, plus call-graph reachability from its April 2025 Coana acquisition. C/C++ support is Conan-only. The CLI is open source; the backend is proprietary.

### Vulnerability Scanners

- **[Grype](https://github.com/anchore/grype)** -- Vulnerability scanner for container images and filesystems. Pairs with Syft for SBOM-based scanning. Doesn't analyze binaries — consumes Syft's package lists and matches them to CVEs.
- **[OSV-Scanner](https://github.com/google/osv-scanner)** -- Google's scanner that matches dependencies against the OSV vulnerability database. Supports lockfile and SBOM input. It-Depends uses the same OSV database for its `--audit` feature.

### Dependency Update Bots

- **[Dependabot](https://github.com/dependabot)** -- GitHub-native bot that opens PRs to update outdated or vulnerable dependencies. Operates as a CI integration, not a standalone analysis tool.
- **[Renovate](https://github.com/renovatebot/renovate)** -- Automated dependency update tool supporting many platforms and ecosystems. Highly configurable. Like Dependabot, it updates dependencies rather than analyzing them.

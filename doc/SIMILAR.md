# Similar Tools

It-Depends is a dependency analyzer that builds complete dependency graphs and SBOMs. Unlike most tools in this space, it can resolve *all possible* dependency versions (not just a single resolution), supports C/C++ projects via cmake/autotools, and maps native library dependencies through dynamic analysis.

## Comparison

| Tool | Type | All-version resolution | C/C++ support | Native lib mapping | SBOM generation | Vuln scanning | Open source |
|------|------|:---:|:---:|:---:|:---:|:---:|:---:|
| **It-Depends** | Dependency analyzer | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| [Syft](https://github.com/anchore/syft) | SBOM generator | ❌ | ✅ | ❌ | ✅ | ❌ | ✅ |
| [Trivy](https://github.com/aquasecurity/trivy) | SBOM generator / scanner | ❌ | 🟨 | ❌ | ✅ | ✅ | ✅ |
| [Grype](https://github.com/anchore/grype) | Vuln scanner | ❌ | 🟨 | ❌ | ❌ | ✅ | ✅ |
| [OSV-Scanner](https://github.com/google/osv-scanner) | Vuln scanner | ❌ | ✅ | ❌ | ✅ | ✅ | ✅ |
| [ORT](https://github.com/oss-review-toolkit/ort) | SCA / compliance | ❌ | 🟨 | ❌ | ✅ | ✅ | ✅ |
| [Snyk](https://snyk.io/) | SCA platform | ❌ | ✅ | ❌ | ✅ | ✅ | ❌ |
| [OWASP Dep-Check](https://github.com/dependency-check/DependencyCheck) | SCA / vuln scanner | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| [Dependabot](https://github.com/dependabot) | Dependency updater | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| [Renovate](https://github.com/renovatebot/renovate) | Dependency updater | ❌ | 🟨 | ❌ | ❌ | ✅ | ✅ |

> *"C/C++ support" means the tool detects C/C++ dependencies via at least one mechanism — e.g. a manifest format (conan, vcpkg), the system package manager (apt, rpm), or source-file hashing. It-Depends additionally resolves dependencies directly from cmake and autotools sources. 🟨 indicates partial support (e.g. Conan-only, or inherited via another tool).*

## Categories

### SBOM Generators

- **[Syft](https://github.com/anchore/syft)** -- Generates SBOMs from container images and filesystems. Supports CycloneDX and SPDX formats. Focused on cataloging what's installed rather than resolving dependency trees.
- **[Trivy](https://github.com/aquasecurity/trivy)** -- All-in-one security scanner for containers, filesystems, and git repositories. Generates SBOMs and scans for vulnerabilities, misconfigurations, and secrets.

### Dependency Analysis / SCA

- **[ORT](https://github.com/oss-review-toolkit/ort)** (OSS Review Toolkit) -- Comprehensive open-source compliance toolchain. Analyzes dependencies, scans for licenses, and generates reports. Broad ecosystem support but resolves a single dependency tree.
- **[Snyk](https://snyk.io/)** -- Commercial SCA platform that monitors dependencies for vulnerabilities. Integrates with CI/CD pipelines and provides fix suggestions. Closed-source core.
- **[OWASP Dependency-Check](https://github.com/dependency-check/DependencyCheck)** -- Identifies known vulnerabilities in project dependencies by cross-referencing against the NVD. Primarily Java-focused but supports other ecosystems.

### Vulnerability Scanners

- **[Grype](https://github.com/anchore/grype)** -- Vulnerability scanner for container images and filesystems. Pairs with Syft for SBOM-based scanning. Doesn't analyze binaries — consumes Syft's package lists and matches them to CVEs.
- **[OSV-Scanner](https://github.com/google/osv-scanner)** -- Google's scanner that matches dependencies against the OSV vulnerability database. Supports lockfile and SBOM input. It-Depends uses the same OSV database for its `--audit` feature.

### Dependency Update Bots

- **[Dependabot](https://github.com/dependabot)** -- GitHub-native bot that opens PRs to update outdated or vulnerable dependencies. Operates as a CI integration, not a standalone analysis tool.
- **[Renovate](https://github.com/renovatebot/renovate)** -- Automated dependency update tool supporting many platforms and ecosystems. Highly configurable. Like Dependabot, it updates dependencies rather than analyzing them.

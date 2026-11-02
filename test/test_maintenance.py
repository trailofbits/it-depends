"""Maintenance checks and their JSON, CLI, and CycloneDX integration."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
import requests
from pydantic import ValidationError
from semantic_version import Version

from it_depends import _cli
from it_depends.cache import PackageRepository
from it_depends.config import Settings
from it_depends.maintenance import check_maintenance_status, extract_github_repo
from it_depends.models import MaintenanceInfo, Package, SourcePackage, Vulnerability
from it_depends.repository import SourceRepository
from it_depends.sbom import SBOM, cyclonedx_to_json


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/owner/widget", ("owner", "widget")),
        ("git@github.com:owner/project.git", ("owner", "project")),
        ("git+https://github.com/owner/project.test.git", ("owner", "project.test")),
        ("ssh://git@github.com/owner/project", ("owner", "project")),
        ("https://github.com/owner/project/tree/main?x=1#readme", ("owner", "project")),
        ("https://github.com.evil.test/owner/project", None),
        ("https://evil.test/github.com/owner/project", None),
        ("https://github.com@evil.test/owner/project", None),
        ("https://github.com/owner/..", None),
        ("https://github.com/owner/a%2fb", None),
        ("https://github.com/owner/", None),
        ("https://[", None),
        ("", None),
    ],
)
def test_repository_urls(url: str, expected: tuple[str, str] | None) -> None:
    assert extract_github_repo(url) == expected


def _repo(*packages: Package) -> PackageRepository:
    repo = PackageRepository()
    for package in packages:
        repo.add(package)
    return repo


def _response(data: object) -> Mock:
    return Mock(json=Mock(return_value=data))


def _commits(date: str) -> list[dict]:
    return [{"commit": {"committer": {"date": date}}}]


@pytest.mark.parametrize(
    ("source", "name", "metadata", "registry"),
    [
        (
            "pip",
            "demo",
            {"info": {"project_urls": {"Source": "https://github.com/owner/widget"}}},
            "https://pypi.org/pypi/demo/json",
        ),
        (
            "pip",
            "demo",
            {"info": {"project_urls": None, "home_page": "https://github.com/owner/widget"}},
            "https://pypi.org/pypi/demo/json",
        ),
        (
            "npm",
            "@scope/demo",
            {"repository": {"url": "git+https://github.com/owner/widget.git"}},
            "https://registry.npmjs.org/%40scope%2Fdemo",
        ),
        ("npm", "demo", {"repository": "https://github.com/owner/widget"}, "https://registry.npmjs.org/demo"),
        (
            "cargo",
            "demo",
            {"crate": {"repository": "https://github.com/owner/widget"}},
            "https://crates.io/api/v1/crates/demo",
        ),
    ],
)
def test_registry_lookup(source: str, name: str, metadata: dict, registry: str) -> None:
    package = Package(name, "1.0.0", source)
    with patch("it_depends.maintenance.requests.get") as get:
        get.side_effect = [_response(metadata), _response(_commits("2000-01-01T00:00:00Z"))]
        check_maintenance_status(_repo(package), github_token="test-token")  # noqa: S106
    assert get.call_args_list[0].args[0] == registry
    assert "Authorization" not in get.call_args_list[0].kwargs["headers"]
    assert get.call_args_list[1].kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert package.maintenance_info is not None
    assert package.maintenance_info.repository_url == "https://github.com/owner/widget"
    assert package.maintenance_info.is_stale is True
    assert package.maintenance_info.error is None


@pytest.mark.parametrize(("days", "stale"), [(364, False), (365, False), (366, True), (-1, False)])
def test_staleness_boundary(days: int, *, stale: bool) -> None:
    package = Package("github.com/owner/widget/submodule/v2", "1.0.0", "go")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    date = (now - timedelta(days=days)).isoformat()
    with (
        patch("it_depends.maintenance.requests.get", return_value=_response(_commits(date))) as get,
        patch("it_depends.maintenance.datetime", wraps=datetime) as clock,
    ):
        clock.now.return_value = now
        repo = _repo(package)
        assert check_maintenance_status(repo) is repo
    assert get.call_args.args[0] == "https://api.github.com/repos/owner/widget/commits?per_page=1"
    assert package.maintenance_info is not None
    assert package.maintenance_info.is_stale is stale
    assert package.maintenance_info.days_since_update == max(0, days)


@pytest.mark.parametrize("data", [[], {}, None, _commits("bad"), _commits("2020-01-01"), _commits(None)])
def test_invalid_commit_response(data: object) -> None:
    package = Package("github.com/owner/widget", "1.0.0", "go")
    with patch("it_depends.maintenance.requests.get", return_value=_response(data)):
        check_maintenance_status(_repo(package))
    assert package.maintenance_info is not None
    assert package.maintenance_info.error
    assert package.maintenance_info.is_stale is None
    assert package.to_obj()["maintenance"]["is_stale"] is None


@pytest.mark.parametrize("status", [403, 404, 409, 500])
def test_http_errors_continue(status: int) -> None:
    first = Package("github.com/owner/first", "1.0.0", "go")
    second = Package("github.com/owner/second", "1.0.0", "go")
    response = requests.Response()
    response.status_code = status
    with patch("it_depends.maintenance.requests.get") as get:
        get.side_effect = [requests.HTTPError(response=response), _response(_commits("2000-01-01T00:00:00Z"))]
        check_maintenance_status(_repo(first, second))
    assert first.maintenance_info is not None
    assert f"HTTP {status}" in str(first.maintenance_info.error)
    assert first.maintenance_info.is_stale is None
    assert second.maintenance_info is not None
    assert second.maintenance_info.is_stale is True


def test_timeout_and_environment_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    package = Package("github.com/owner/widget", "1.0.0", "go")
    with patch("it_depends.maintenance.requests.get", side_effect=requests.Timeout) as get:
        check_maintenance_status(_repo(package))
    assert get.call_args.kwargs["timeout"] == 10  # noqa: PLR2004
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert package.maintenance_info is not None
    assert package.maintenance_info.is_stale is None
    assert "test-token" not in package.dumps()


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        [],
        {"info": None},
        {"info": {"project_urls": []}},
        {"info": {"project_urls": {"Source": "https://github.com.evil.test/owner/repo"}}},
        {"info": {"project_urls": {"Source": "https://evil.test/github.com/owner/repo"}}},
        {"info": {"project_urls": {"Source": "https://github.com@evil.test/owner/repo"}}},
    ],
)
def test_bad_registry_metadata(metadata: object) -> None:
    package = Package("demo", "1.0.0", "pip")
    with patch("it_depends.maintenance.requests.get", return_value=_response(metadata)) as get:
        check_maintenance_status(_repo(package))
    get.assert_called_once()
    assert package.maintenance_info is not None
    assert package.maintenance_info.error
    assert package.maintenance_info.is_stale is None


def test_unsupported_and_local_packages_do_not_request() -> None:
    local = SourcePackage("demo", Version("1.0.0"), SourceRepository("."), "pip")
    repo = _repo(local, Package("demo", "1.0.0", "ubuntu"), Package("golang.org/x/tools", "1.0.0", "go"))
    with patch("it_depends.maintenance.requests.get") as get:
        check_maintenance_status(repo)
    get.assert_not_called()
    assert all(p.maintenance_info and p.maintenance_info.error for p in repo)


def test_threshold_validation() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        check_maintenance_status(PackageRepository(), stale_threshold_days=-1)
    with pytest.raises(ValidationError):
        Settings(_cli_parse_args=["pip:demo", "--stale-threshold", "-1"])


def test_optional_json_and_cyclonedx_properties() -> None:
    root = Package("root", "1.0.0", "pip")
    child = Package("child", "1.0.0", "pip")
    assert "maintenance" not in root.to_obj()
    root.maintenance_info = MaintenanceInfo(is_stale=False, days_since_update=0)
    child.maintenance_info = MaintenanceInfo(error="No GitHub repository found")
    output = json.loads(cyclonedx_to_json(SBOM([(root, child)], [root]).to_cyclonedx()))
    components = {c["name"]: {p["name"]: p["value"] for p in c["properties"]} for c in output["components"]}
    assert components["root"]["maintenance:is_stale"] == "false"
    assert components["root"]["maintenance:days_since_update"] == "0"
    assert "maintenance:is_stale" not in components["child"]
    assert components["child"]["maintenance:error"] == child.maintenance_info.error
    assert json.loads(root.dumps())["maintenance"]["is_stale"] is False


@pytest.mark.parametrize("enabled", [False, True])
def test_cli_maintenance_opt_in(*, enabled: bool) -> None:
    settings = Settings(
        _cli_parse_args=["pip:demo", *(["--check-maintenance", "--stale-threshold", "12"] if enabled else [])]
    )
    repo = _repo(Package("demo", "1.0.0", "pip"))
    with (
        patch.object(_cli, "Settings", return_value=settings),
        patch.object(_cli, "setup_logger"),
        patch.object(_cli, "DBPackageCache"),
        patch.object(_cli, "resolve", return_value=repo),
        patch.object(_cli, "_write_output"),
        patch.object(_cli, "check_maintenance_status", return_value=repo) as check,
    ):
        _cli.main()
    if enabled:
        check.assert_called_once_with(repo, stale_threshold_days=12)
    else:
        check.assert_not_called()


@pytest.mark.parametrize(
    "name",
    [
        "github.com.evil.test/owner/repo",
        "evil.test/github.com/owner/repo",
        "github.com@evil.test/owner/repo",
        "github.com/owner/..",
        "github.com/owner/repo%2f..",
        "github.com/owner",
    ],
)
def test_go_untrusted_module_paths_do_not_request(name: str) -> None:
    package = Package(name, "1.0.0", "go")
    with patch("it_depends.maintenance.requests.get") as get:
        check_maintenance_status(_repo(package))
    get.assert_not_called()
    assert package.maintenance_info is not None
    assert package.maintenance_info.error == "No GitHub repository found"
    assert package.maintenance_info.is_stale is None


@pytest.mark.parametrize(
    "info",
    [
        None,
        MaintenanceInfo(is_stale=False, days_since_update=0),
        MaintenanceInfo(is_stale=True, days_since_update=500),
        MaintenanceInfo(error="Unknown repository"),
    ],
)
def test_cli_json_preserves_maintenance_and_existing_fields(info: MaintenanceInfo | None) -> None:
    package = Package("demo", "1.0.0", "pip", vulnerabilities=[Vulnerability("CVE-DEMO", ["alias"], "summary")])
    package.maintenance_info = info
    output: list[str] = []
    # Exercise the real CLI serialization path rather than mocking it out.
    _cli._write_output(  # noqa: SLF001
        Settings(_cli_parse_args=["pip:demo"]), Mock(), _repo(package), None, output_write=output.append
    )
    record = json.loads(output[0])["pip:demo"]["1.0.0"]
    assert record["is_source_package"] is False
    assert record["vulnerabilities"] == ["CVE-DEMO (alias)"]
    if info is not None:
        assert record["maintenance"]["is_stale"] is info.is_stale
        assert record["maintenance"]["days_since_update"] == info.days_since_update
        assert record["maintenance"]["error"] == info.error
    else:
        assert "maintenance" not in record


def test_versions_share_lookups_and_results_are_independent() -> None:
    packages = [Package("demo", f"1.0.{i}", "pip") for i in range(3)]
    with patch("it_depends.maintenance.requests.get") as get:
        get.side_effect = [
            _response({"info": {"project_urls": {"Source": "https://github.com/owner/repo"}}}),
            _response(_commits("2000-01-01T00:00:00Z")),
        ]
        check_maintenance_status(_repo(*packages))
    assert get.call_count == 2  # noqa: PLR2004
    assert all(p.maintenance_info and p.maintenance_info.is_stale for p in packages)
    assert packages[0].maintenance_info is not packages[1].maintenance_info


def test_packages_sharing_repository_fetch_github_once() -> None:
    packages = [Package("first", "1.0.0", "pip"), Package("second", "1.0.0", "npm")]
    with patch("it_depends.maintenance.requests.get") as get:
        get.side_effect = [
            _response({"info": {"home_page": "https://github.com/Owner/Repo"}}),
            _response(_commits("2000-01-01T00:00:00Z")),
            _response({"repository": "https://github.com/owner/repo.git"}),
        ]
        check_maintenance_status(_repo(*packages))
    assert get.call_count == 3  # noqa: PLR2004
    assert all(p.maintenance_info and p.maintenance_info.is_stale for p in packages)


def test_failed_registry_lookup_is_not_repeated_for_versions() -> None:
    packages = [Package("demo", f"1.0.{i}", "pip") for i in range(3)]
    with patch("it_depends.maintenance.requests.get", side_effect=requests.Timeout) as get:
        check_maintenance_status(_repo(*packages))
    get.assert_called_once()
    assert all(p.maintenance_info and p.maintenance_info.error for p in packages)


@pytest.mark.parametrize(
    ("status", "headers"),
    [(429, {"Retry-After": "60"}), (403, {"X-RateLimit-Remaining": "0"}), (403, {"Retry-After": "60"})],
)
def test_rate_limit_stops_github_requests(status: int, headers: dict[str, str]) -> None:
    packages = [Package(f"github.com/owner/repo{i}", "1.0.0", "go") for i in range(3)]
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers)
    with patch("it_depends.maintenance.requests.get", side_effect=requests.HTTPError(response=response)) as get:
        check_maintenance_status(_repo(*packages))
    get.assert_called_once()
    for package in packages:
        assert package.maintenance_info is not None
        assert package.maintenance_info.is_stale is None
        assert "rate limit" in str(package.maintenance_info.error).lower()
        assert "GITHUB_TOKEN" in str(package.maintenance_info.error)


def test_lookup_cache_is_scoped_to_one_run() -> None:
    package = Package("github.com/owner/repo", "1.0.0", "go")
    with patch("it_depends.maintenance.requests.get", return_value=_response(_commits("2000-01-01T00:00:00Z"))) as get:
        check_maintenance_status(_repo(package))
        check_maintenance_status(_repo(package))
    assert get.call_count == 2  # noqa: PLR2004


def test_registry_throttling_does_not_disable_github() -> None:
    registry_package = Package("demo", "1.0.0", "pip")
    github_package = Package("github.com/owner/repo", "1.0.0", "go")
    response = requests.Response()
    response.status_code = 429
    with patch("it_depends.maintenance.requests.get") as get:
        get.side_effect = [requests.HTTPError(response=response), _response(_commits("2000-01-01T00:00:00Z"))]
        check_maintenance_status(_repo(registry_package, github_package))
    assert registry_package.maintenance_info is not None
    assert registry_package.maintenance_info.is_stale is None
    assert github_package.maintenance_info is not None
    assert github_package.maintenance_info.is_stale is True


def test_cached_success_survives_rate_limit_and_next_run_retries() -> None:
    first = Package("github.com/owner/first", "1.0.0", "go")
    limited = Package("github.com/owner/limited", "1.0.0", "go")
    same_repo = Package("github.com/owner/first/submodule", "1.0.0", "go")
    response = requests.Response()
    response.status_code = 429
    with patch("it_depends.maintenance.requests.get") as get:
        get.side_effect = [_response(_commits("2000-01-01T00:00:00Z")), requests.HTTPError(response=response)]
        check_maintenance_status(_repo(first, limited, same_repo))
    assert get.call_count == 2  # noqa: PLR2004
    assert same_repo.maintenance_info is not None
    assert same_repo.maintenance_info.is_stale is True
    with patch("it_depends.maintenance.requests.get", return_value=_response(_commits("2000-01-01T00:00:00Z"))) as get:
        check_maintenance_status(_repo(limited))
    get.assert_called_once()
    assert limited.maintenance_info is not None
    assert limited.maintenance_info.is_stale is True


def test_secondary_rate_limit_without_headers_stops_requests() -> None:
    packages = [Package(f"github.com/owner/repo{i}", "1.0.0", "go") for i in range(2)]
    response = requests.Response()
    response.status_code = 403
    response._content = b'{"message": "You have exceeded a secondary rate limit."}'  # noqa: SLF001
    with patch("it_depends.maintenance.requests.get", side_effect=requests.HTTPError(response=response)) as get:
        check_maintenance_status(_repo(*packages))
    get.assert_called_once()
    assert all(p.maintenance_info and p.maintenance_info.is_stale is None for p in packages)

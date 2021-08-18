"""
Rebuilds repos/*.expected.json by running the tests in a Docker container to match how they would be run in CI
"""

from pathlib import Path
from typing import Optional

from it_depends.docker import DockerContainer, Dockerfile

from test_smoke import IT_DEPENDS_DIR, SmokeTest, SMOKE_TESTS


CI_TEST_PATH: Path = Path(__file__).parent.parent / ".github" / "workflows" / "tests.yml"
_CONTAINER: Optional[DockerContainer] = None


def container_type() -> str:
    """Returns the Docker container name used in GitHub CI"""
    if not CI_TEST_PATH.exists():
        raise ValueError(f"GitHub action file {CI_TEST_PATH!s} does not exist!")
    with open(CI_TEST_PATH, "r") as f:
        for line in f.readlines():
            line = line.strip()
            if line.startswith("runs-on:"):
                github_name = line[len("runs-on:"):].lstrip()
                hyphen_index = github_name.find("-")
                if hyphen_index < 0:
                    raise ValueError(f"Unknown runs-on: container type {github_name!r} in {CI_TEST_PATH}")
                return f"{github_name[:hyphen_index]}:{github_name[hyphen_index+1:]}"
    raise ValueError(f"Did not find `runs-on: ...` line in {CI_TEST_PATH}")


def get_container() -> DockerContainer:
    global _CONTAINER
    if _CONTAINER is None:
        dockerfile = Dockerfile(IT_DEPENDS_DIR / "Dockerfile")
        dockerfile_existed = dockerfile.exists()
        try:
            if not dockerfile_existed:
                with open(dockerfile.path, "w") as f:
                    f.write(f"""FROM {container_type()}

                    RUN DEBIAN_FRONTEND=noninteractive apt-get update && \\
                        DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-dev python3-pip docker \\
                        cmake autoconf golang cargo npm clang libz3-dev \\
                        && mkdir -p /it-depends
                    # this is required for cargo:
                    ENV USER=root
                    COPY . /it-depends
                    WORKDIR /it-depends
                    RUN pip3 install .
                    """)
            _CONTAINER = DockerContainer("trailofbits/it-depends", dockerfile=dockerfile, tag="latest")
            _CONTAINER.rebuild()
        finally:
            if not dockerfile_existed and dockerfile.exists():
                dockerfile.path.unlink()
    return _CONTAINER


def rebuild(test: SmokeTest):
    print(f"Rebuilding {test.expected_json!s}")
    container = get_container()
    if container.run(
        "it-depends", str(test.snapshot_folder.relative_to(IT_DEPENDS_DIR)), "-f", "json",
        "-o", str(test.expected_json.relative_to(IT_DEPENDS_DIR)), "--force",
        cwd=IT_DEPENDS_DIR,
        check_existence=False, rebuild=False, mounts=(
            (test.expected_json.parent, "/it-depends/test/repos"),
            ("/var/run/docker.sock", "/var/run/docker.sock"),
        ),
        privileged=True
    ) != 0:
        raise ValueError(f"it-depends exited with non-zero status for {test.snapshot_folder}!")
    print(f"Updated {test.expected_json!s}")


if __name__ == "__main__":
    for t in sorted(SMOKE_TESTS, key=lambda st: st.repo_name):
        rebuild(t)

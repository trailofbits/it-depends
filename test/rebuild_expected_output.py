"""
Rebuilds repos/*.expected.json by running the tests in a Docker container to match how they would be run in CI
"""

from __future__ import annotations

import logging
from pathlib import Path

from test_smoke import IT_DEPENDS_DIR, SMOKE_TESTS, SmokeTest

from it_depends.docker import DockerContainer, Dockerfile

CI_TEST_PATH: Path = Path(__file__).parent.parent / ".github" / "workflows" / "tests.yml"
_CONTAINER: DockerContainer | None = None

logger = logging.getLogger(__name__)


def container_type() -> str:
    """Returns the Docker container name used in GitHub CI"""
    if not CI_TEST_PATH.exists():
        msg = "GitHub action file %s does not exist"
        raise ValueError(msg % CI_TEST_PATH)
    with CI_TEST_PATH.open() as f:
        lines = [line.strip() for line in f]
        for line in lines:
            if line.startswith("runs-on:"):
                github_name = line[len("runs-on:") :].lstrip()
                hyphen_index = github_name.find("-")
                if hyphen_index < 0:
                    msg = "Unknown runs-on: container type %s in %s"
                    raise ValueError(msg % (github_name, CI_TEST_PATH))
                return f"{github_name[:hyphen_index]}:{github_name[hyphen_index + 1 :]}"
    msg = "Did not find `runs-on: ...` line in %s"
    raise ValueError(msg % CI_TEST_PATH)


def get_container() -> DockerContainer:
    global _CONTAINER  # noqa: PLW0603
    if _CONTAINER is None:
        dockerfile = Dockerfile(IT_DEPENDS_DIR / "Dockerfile")
        dockerfile_existed = dockerfile.exists()
        try:
            if not dockerfile_existed:
                with dockerfile.path.open("w") as f:
                    f.write_text(f"""FROM {container_type()}

                    RUN DEBIAN_FRONTEND=noninteractive apt-get update && \\
                        DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-dev python3-pip docker.io \\
                        cmake autoconf golang cargo npm clang \\
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


def rebuild(test: SmokeTest) -> None:
    logger.info("Rebuilding %s", test.expected_json)
    container = get_container()
    if (
        container.run(
            "it-depends",
            str(test.snapshot_folder.relative_to(IT_DEPENDS_DIR)),
            "-f",
            "json",
            "-o",
            str(test.expected_json.relative_to(IT_DEPENDS_DIR)),
            "--force",
            cwd=IT_DEPENDS_DIR,
            check_existence=False,
            rebuild=False,
            mounts=(
                (test.expected_json.parent, "/it-depends/test/repos"),
                ("/var/run/docker.sock", "/var/run/docker.sock"),
            ),
            privileged=True,
        )
        != 0
    ):
        msg = "it-depends exited with non-zero status for %s"
        raise ValueError(msg % test.snapshot_folder)
    logger.info("Updated %s", test.expected_json)


if __name__ == "__main__":
    for t in sorted(SMOKE_TESTS, key=lambda st: st.repo_name):
        rebuild(t)

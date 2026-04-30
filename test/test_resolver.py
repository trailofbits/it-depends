import logging
from unittest import TestCase

import pytest

from it_depends.dependencies import Dependency, resolve, resolve_sbom
from it_depends.sbom import cyclonedx_to_json

logger = logging.getLogger(__name__)


class TestResolver(TestCase):
    @pytest.mark.integration
    def test_resolve(self) -> None:
        dep = Dependency.from_string("cargo:rand_core@0.6.2")
        packages = resolve(dep, depth_limit=1)
        for package in packages:
            if not package.dependencies:
                continue
            # NOTE(@evandowning): Test the newest resolution. The oldest takes too long to find.
            for sbom in resolve_sbom(package, packages, order_ascending=False):
                logger.info(cyclonedx_to_json(sbom.to_cyclonedx()))
                break
            break

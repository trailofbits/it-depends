import logging
from unittest import TestCase

import pytest

from it_depends.dependencies import resolve_sbom
from it_depends.sbom import cyclonedx_to_json

from .test_smoke import SmokeTest

logger = logging.getLogger(__name__)


class TestResolver(TestCase):
    @pytest.mark.integration
    def test_resolve(self) -> None:
        test = SmokeTest("trailofbits", "it-depends", "56be3121643cb7e5ba6ecceb77b0b0cce286c2f7")
        packages = test.run()
        for package in packages.source_packages:
            # NOTE(@evandowning): Test the newest resolution. The oldest takes too long to find.
            for sbom in resolve_sbom(package, packages, order_ascending=False):
                logger.info(cyclonedx_to_json(sbom.to_cyclonedx()))
                break

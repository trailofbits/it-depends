import logging
from unittest import TestCase

from it_depends.dependencies import resolve_sbom
from it_depends.sbom import cyclonedx_to_json

from .test_smoke import SmokeTest

logger = logging.getLogger(__name__)


class TestResolver(TestCase):
    def test_resolve(self) -> None:
        test = SmokeTest("trailofbits", "it-depends", "3db3d191ce04fb8a19bcc5c000ce84dbb3243f31")
        packages = test.run()
        for package in packages.source_packages:
            for sbom in resolve_sbom(package, packages, order_ascending=True):
                logger.info(cyclonedx_to_json(sbom.to_cyclonedx()))
                break

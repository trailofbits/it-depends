from unittest import TestCase

from it_depends.vcs import resolve


class TestVCS(TestCase):
    def test_resolve(self) -> None:
        repo = resolve("github.com/trailofbits/graphtage")
        assert repo.vcs.name == "Git"

from unittest import TestCase

from it_depends.vcs import resolve


class TestVCS(TestCase):
    def test_resolve(self):
        repo = resolve("github.com/trailofbits/graphtage")
        self.assertEqual(repo.vcs.name, "Git")

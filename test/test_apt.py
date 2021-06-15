from unittest import TestCase

from it_depends.apt import file_to_package


class TestAPT(TestCase):
    def test_file_to_package(self):
        self.assertEqual(file_to_package("/usr/bin/python3"), "python3-minimal")

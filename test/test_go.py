from unittest import TestCase

from it_depends.go import GoModule, GoSpec, GoVersion


EXAMPLE_MOD = """
module github.com/btcsuite/btcd

require (
\tgithub.com/aead/siphash v1.0.1 // indirect
\tgithub.com/btcsuite/btclog v0.0.0-20170628155309-84c8d2346e9f
\tgithub.com/btcsuite/btcutil v0.0.0-20190425235716-9e5f4b9a998d
\tgithub.com/btcsuite/go-socks v0.0.0-20170105172521-4720035b7bfd
\tgithub.com/btcsuite/goleveldb v0.0.0-20160330041536-7834afc9e8cd
\tgithub.com/btcsuite/snappy-go v0.0.0-20151229074030-0bdef8d06723 // indirect
\tgithub.com/btcsuite/websocket v0.0.0-20150119174127-31079b680792
\tgithub.com/btcsuite/winsvc v1.0.0
\tgithub.com/davecgh/go-spew v0.0.0-20171005155431-ecdeabc65495
\tgithub.com/jessevdk/go-flags v0.0.0-20141203071132-1679536dcc89
\tgithub.com/jrick/logrotate v1.0.0
\tgithub.com/kkdai/bstream v0.0.0-20161212061736-f391b8402d23 // indirect
\tgithub.com/onsi/ginkgo v1.7.0 // indirect
\tgithub.com/onsi/gomega v1.4.3 // indirect
\tgolang.org/x/crypto v0.0.0-20170930174604-9419663f5a44
)

go 1.12
"""


class TestGo(TestCase):
    def test_load_from_github(self):
        GoModule.from_git("github.com/golang/protobuf", "https://github.com/golang/protobuf", tag="v1.4.3")

    def test_parsing(self):
        module = GoModule.parse_mod(EXAMPLE_MOD)
        self.assertEqual(module.name, "github.com/btcsuite/btcd")
        self.assertEqual(len(module.dependencies), 15)
        self.assertIn(("github.com/btcsuite/websocket", "v0.0.0-20150119174127-31079b680792"), module.dependencies)

    def test_version_parsing(self):
        for _, version in GoModule.parse_mod(EXAMPLE_MOD).dependencies:
            self.assertEqual(str(GoVersion(version)), version)
            self.assertEqual(str(GoSpec(version)), version)

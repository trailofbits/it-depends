# it-depends

`it-depends` produces the list of dependencies from a source code repository. Supporting JavaScript, Rust, Python and C/C++ projects.

## Slow Start 🐢
Currently `it-depends` runs only in `ubuntu` and needs to be installed from source.
Different language plugins require having certain tools installed and accessible from the PATH.
Eventually all these requirements will be handled via container magic.

### Deps 🎭

JavaScript needs `npm`\
Rust needs `cargo`\
Python needs `pip`\
C/C++ needs `autotools` and/or `cmake`\
Several native dependencies are solved using ubuntu file to path database `apt-file`\
Currently `docker` is used to resolve native dependencies

### Install 🚀
```console
$ git clone https://github.com/trailofbits/it-depends
$ cd it-depends
$ python3 -m venv venv  # Optional virtualenv
$ ./venv/bin/activate   # Optional virtualenv
$ python setup.py install
```

If everything is good `it-depends` can be run over any _supported_ source code repositories.
For example, it can be run on itself:

### Running it 🏃
You simply point it to a repository. Here it is run on itself.  
```console
$ it-depends .
```
[![demo]()](https://gist.githubusercontent.com/feliam/e906ce723333b2b55237a71c4028559e/raw/e60f46c35b215a73a37a1d1ce3bb43eaead76af4/it-depends-demo.svg?sanitize=1)

This is the resultant [json](https://gist.github.com/feliam/2bdec76f7aa50602869059bfa14df156) 
with all the discovered dependencies.
This is the resultant [dot](https://gist.github.com/feliam/275951f5788c23a477bc7cf758a32cc2)
[![It-depends dep graph]()](https://user-images.githubusercontent.com/1017522/116887041-33903b80-ac00-11eb-9288-f3d286231e47.png)

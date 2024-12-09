import os
from setuptools import setup, find_packages

SETUP_DIR = os.path.dirname(os.path.realpath(__file__))
README_PATH = os.path.join(SETUP_DIR, "README.md")

with open(README_PATH, "r") as readme:
    README = readme.read()

setup(
    name="it-depends",
    description="A software dependency analyzer",
    long_description=README,
    long_description_content_type="text/markdown",
    license="LGPL-3.0-or-later",
    url="https://github.com/trailofbits/it-depends",
    author="Trail of Bits",
    version="0.1.2",
    packages=find_packages(exclude=["test"]),
    python_requires=">=3.7",
    install_requires=[
        "appdirs>=1.4.4",
        "cyclonedx-python-lib >= 5,< 9",
        "docker>=4.4.0",
        "graphviz>=0.14.1",
        "johnnydep>=1.8",
        "networkx>=2.4",
        "parse_cmake>=0.4.1",
        "semantic_version~=2.8.5",
        "sqlalchemy>=1.3",
        "tqdm>=4.48.0",
        # Indirect dependencies for which we pin a minimum version to mitigate vulnerabilities:
        "requests>=2.20.0",  # CVE-2018-18074
        "urllib3>=1.26.5",  # CVE-2021-33503
    ],
    extras_require={
        "dev": ["flake8", "pytest", "twine", "mypy>=0.812", "types-setuptools", "types-requests"]
    },
    entry_points={
        "console_scripts": [
            "it-depends = it_depends.__main__:main"
        ]
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Science/Research",
        "License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)",
        "Programming Language :: Python :: 3 :: Only",
        "Topic :: Utilities"
    ]
)

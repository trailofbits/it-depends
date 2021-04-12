from setuptools import setup, find_packages

setup(
    name="it-depends",
    description="A software dependency analyzer",
    license="LGPL-3.0-or-later",
    url="https://github.com/trailofbits/it-depends",
    author="Trail of Bits",
    version="0.0.1",
    packages=find_packages(exclude=["test"]),
    python_requires=">=3.7",
    install_requires=[
        "docker>=4.4.0",
        "johnnydep~=1.7",
        "parse_cmake",
        "semantic_version~=2.8.5",
        "sqlalchemy>=1.3",
        "tqdm>=4.48.0"
    ],
    extras_require={
        "dev": ["flake8", "pytest", "twine", "mypy"]
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

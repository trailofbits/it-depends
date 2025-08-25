SHELL := /bin/bash

PY_IMPORT = it_depends

ALL_PY_SRCS := $(shell find src -name '*.py') \
	$(shell find test -name '*.py')

# Optionally overriden by the user, if they're using a virtual environment manager.
# Warning: changing this name to something else than '.venv' will make working with
# uv harder.
VENV ?= .venv

# On Windows, venv scripts/shims are under `Scripts` instead of `bin`.
VENV_BIN := $(VENV)/bin
ifeq ($(OS),Windows_NT)
	VENV_BIN := $(VENV)/Scripts
endif

# Optionally overridden by the user in the `release` target.
BUMP_ARGS :=

# Optionally overridden by the user in the `test` target.
TESTS :=

# Optionally overridden by the user/CI, to limit the installation to a specific
# subset of development dependencies.
INSTALL_EXTRA := dev

# If the user selects a specific test pattern to run, set `pytest` to fail fast
# and only run tests that match the pattern.
# Otherwise, run all tests and enable coverage assertions, since we expect
# complete test coverage.
ifneq ($(TESTS),)
	TEST_ARGS := -x -k $(TESTS)
	COV_ARGS :=
else
	TEST_ARGS :=
	COV_ARGS := --fail-under 90
endif

.PHONY: all
all:
	@echo "Run my targets individually!"

.PHONY: dev
dev: $(VENV)/pyvenv.cfg
.PHONY: run
run: $(VENV)/pyvenv.cfg
	uv run it-depends $(ARGS)

$(VENV)/pyvenv.cfg: pyproject.toml
	uv venv $(VENV)
	uv pip install -e '.[$(INSTALL_EXTRA)]'

.PHONY: lint
lint: $(VENV)/pyvenv.cfg
	uv run ruff format --check && \
		uv run ruff check && \
		uv run mypy
		uv run interrogate -c pyproject.toml .

.PHONY: reformat
reformat:
	uv run ruff format && \
		uv run ruff check --fix

.PHONY: test tests
test tests: $(VENV)/pyvenv.cfg
	uv run pytest --cov=$(PY_IMPORT) $(T) $(TEST_ARGS)
	uv run coverage report -m $(COV_ARGS)

.PHONY: doc
doc: $(VENV)/pyvenv.cfg
	uv run pdoc -o html $(PY_IMPORT)

.PHONY: package
package: $(VENV)/pyvenv.cfg
	uv build

.PHONY: edit
edit:
	$(EDITOR) $(ALL_PY_SRCS)

# pytest-conda-solvers

[![PyPI version](https://img.shields.io/pypi/v/pytest-conda-solvers.svg)](https://pypi.org/project/pytest-conda-solvers)
[![Python versions](https://img.shields.io/pypi/pyversions/pytest-conda-solvers.svg)](https://pypi.org/project/pytest-conda-solvers)

A [pytest](https://github.com/pytest-dev/pytest) plugin that runs shared conda solver YAML tests against classic, libmamba, or other conda solver backends.

The PyPI package ships the plugin, fixtures, mock channel data, and shared YAML
suite under `pytest_conda_solvers/`. When the plugin loads, it automatically adds
the bundled suite to pytest's collection.

`conda-solver-tests` pytest plugin was orginally generated with [Cookiecutter] along with [@hackebrot]'s [cookiecutter-pytest-plugin] template.

## Error assertion semantics

For unsatisfiable tests, this suite deliberately asserts more than conda's own
test suite does when running under conda-libmamba-solver. Upstream's
`assert_unsatisfiable` helper only checks the exception is an
`UnsatisfiableError` subclass there, because its entries comparison is gated on
the exact `UnsatisfiableError` type. Our runner additionally checks that the
endpoint package names of each expected conflict chain appear in the libmamba
error message, and `message_includes`/`message_excludes` fields in the YAML add
further content checks. This strengthening is intentional: cross-solver
consistency of error reporting is part of what this plugin exists to verify.

## Requirements

- Python ≥ 3.10
- A working [conda](https://docs.conda.io/) (>=26.3.0) installation in the environment (install via conda, mamba, or pixi — not from PyPI)
- The solver backend under test (for example `conda-libmamba-solver`)

## Installation

```bash
conda create -n solver-test-env conda pip
conda activate solver-test-env
pip install pytest-conda-solvers
```

For local development from a checkout:

```bash
pixi install
# or: pip install -e .
```

## Usage

Select a solver with `--conda-solver`; the bundled suite is collected automatically:

```bash
pytest --conda-solver=libmamba
pytest --conda-solver=classic
```

Paths passed to pytest are collected alongside the bundled suite, so solver
authors can add their own Python or YAML tests normally.

Use `--no-bundled-solver-tests` to collect custom tests without the suite
shipped by this package.

From this repository with pixi:

```bash
pixi run test-libmamba-solver
pixi run test-classic-solver
```

The plugin collects solver-suite `.yaml` files, starts a session-scoped mock channel server, and runs each case through the selected solver backend.

## Contributing

Contributions are welcome. It is recommended to use [pixi](https://pixi.sh/) for the development environment and run the solver test tasks above before opening a pull request.

## License

Distributed under the [BSD-3-Clause](https://opensource.org/licenses/BSD-3-Clause) license.

## Issues

If you encounter problems, please [file an issue](https://github.com/conda-incubator/pytest-conda-solvers/issues).

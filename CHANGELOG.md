# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.0 (2026-08-05)

First public release of `pytest-conda-solvers`.

### Added

- Pytest plugin that collects shared solver YAML tests and runs them against conda solver backends via `--conda-solver`
- Session-scoped mock HTTP channel server and bundled fixture channel data
- Shared solver YAML suite shipped in the wheel under `pytest_conda_solvers/conda-solver-tests/`
- `solver_tests_path()` API for locating the bundled suite, which the plugin automatically adds to pytest collection
- `--no-bundled-solver-tests` option for collecting custom tests without the bundled suite
- Packaging for PyPI with hatchling and hatch-vcs
- Support for Python 3.10 and newer

### Notes

- `conda` is not declared as a PyPI dependency; install conda (and the solver under test) via conda, mamba, or pixi before using the plugin

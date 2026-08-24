"""Locate bundled resources shipped with the package."""

from pathlib import Path


def solver_tests_path() -> Path:
    """Return the filesystem path to the bundled shared solver YAML suite."""
    return Path(__file__).parent / "conda-solver-tests"

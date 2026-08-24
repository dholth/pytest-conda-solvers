"""
Collect the upstream conda pytest node IDs for every test we have ported to YAML.

Each YAML entry in pytest_conda_solvers/conda-solver-tests/*.yaml records where
it came from in its provenance.node_id field. This script turns those provenance
IDs into runnable conda/conda pytest node IDs.

Some mapping rules:
  1. tests/core/test_solve.py::<func> (and any other tests/...::<func>) is
     kept as-is. In conda, `tests/core/test_solve.py` uses the
     `parametrized_solver_fixture`, so a bare `file::func` ID runs both the
     classic and libmamba parametrisations (controlled by CONDA_TEST_SOLVERS).
  2. conda/testing/solver_helpers.py::SolverTests.<method> maps to the two
     subclasses that conda runs it under:
       - tests/test_solvers.py::TestClassicSolver::<method>
       - tests/test_solvers.py::TestLibMambaSolver::<method>
  3. A trailing ::<digits> sub-index (which, in this case, is our own splitting
     of a singular conda test that performs several solves) is stripped before mapping.

Node IDs listed in tools/conda-upstream-skips.txt are excluded from the output.

The output is one node ID per line, sorted, with paths relative to the conda
checkout root. Run from the repository root.

Usage:
    python tools/collect_ported_node_ids.py
"""

import re
from pathlib import Path

import msgspec

from pytest_conda_solvers.models import TestModule

REPO_ROOT = Path(__file__).parent.parent
YAML_DIR = REPO_ROOT / "pytest_conda_solvers" / "conda-solver-tests"

# Node IDs to exclude from the upstream run
SKIPS_FILE = Path(__file__).parent / "conda-upstream-skips.txt"

# The base class in conda/testing/solver_helpers.py and the subclasses in
# tests/test_solvers.py that conda runs it under (one per solver).
SOLVER_HELPERS_FILE = "conda/testing/solver_helpers.py"
SOLVER_TESTS_FILE = "tests/test_solvers.py"
SOLVER_TESTS_CLASSES = ("TestClassicSolver", "TestLibMambaSolver")

# Trailing "::<digits>" sub-index we add to distinguish multiple solves within a
# single upstream test function.
_SUB_INDEX = re.compile(r"::\d+$")


def _load_skips() -> set[str]:
    skips: set[str] = set()
    for line in SKIPS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            skips.add(line)
    return skips


def _iter_node_ids():
    for yaml_path in sorted(YAML_DIR.glob("*.yaml")):
        module = msgspec.yaml.decode(yaml_path.read_bytes(), type=TestModule)
        for test in module.tests:
            yield test.provenance.node_id


def _map_node_id(node_id: str) -> list[str]:
    node_id = _SUB_INDEX.sub("", node_id)
    file, _, rest = node_id.partition("::")
    if file == SOLVER_HELPERS_FILE:
        # The class/method separator appears in both accepted provenance forms,
        # SolverTests.test_iopro_mkl and SolverTests::test_iopro_mkl (see
        # tools/update_provenance.py), so split on either.
        method = re.split(r"::|\.", rest, maxsplit=1)[1]
        return [f"{SOLVER_TESTS_FILE}::{cls}::{method}" for cls in SOLVER_TESTS_CLASSES]
    return [node_id]


def collect() -> list[str]:
    ids: set[str] = set()
    for node_id in _iter_node_ids():
        ids.update(_map_node_id(node_id))
    return sorted(ids - _load_skips())


def main() -> None:
    for node_id in collect():
        print(node_id)


if __name__ == "__main__":
    main()

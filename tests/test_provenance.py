"""
This are some meta-tests to validate provenance entries in our YAML test files.

Here is a list of what we check:
- URL embeds the same commit SHA as the provenance.commit field,
- URL file path matches the node_id file path,
- URL has a valid #L{start}-L{end} line range fragment,
- That there are no duplicate test IDs or names across all YAML files
- That the referenced source file exists at the given commit on GitHub (TODO: uses networking capabilities, could be cached more or perhaps we can drop it for simplicity later)
- That the line range in the URL matches the test function boundaries
"""

import ast
import functools
import re
import urllib.request

import msgspec
import pytest

from pytest_conda_solvers.models import TestModule as _TestModule
from pytest_conda_solvers.paths import solver_tests_path

YAML_DIR = solver_tests_path()

URL_PATTERN = re.compile(
    r"^https://github\.com/(?P<org>[^/]+)/(?P<repo>[^/]+)/blob/"
    r"(?P<commit>[0-9a-f]+)/(?P<path>[^#]+)"
    r"(?:#L(?P<start>\d+)-L(?P<end>\d+))?$"
)


def _load_all_tests():
    for yaml_path in sorted(YAML_DIR.glob("*.yaml")):
        data = yaml_path.read_text(encoding="utf-8")
        module = msgspec.yaml.decode(data, type=_TestModule)
        for test in module.tests:
            yield yaml_path.name, test


ALL_TESTS = list(_load_all_tests())


@pytest.fixture(params=ALL_TESTS, ids=[f"{f}::{t.id}" for f, t in ALL_TESTS])
def test_entry(request):
    return request.param


@functools.cache
def _fetch_source(commit: str, filepath: str) -> str:
    url = f"https://raw.githubusercontent.com/conda/conda/{commit}/{filepath}"
    with urllib.request.urlopen(url) as resp:
        return resp.read().decode("utf-8")


@functools.cache
def _get_function_lines(commit: str, filepath: str) -> dict[str, tuple[int, int]]:
    source = _fetch_source(commit, filepath)
    tree = ast.parse(source)
    funcs = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    funcs[f"{node.name}.{item.name}"] = (
                        item.lineno,
                        item.end_lineno,
                    )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[node.name] = (node.lineno, node.end_lineno)
    return funcs


# ---------------------------------------------------------------------------
# Formatting and consistency checks
# ---------------------------------------------------------------------------


class TestProvenanceFormat:
    def test_url_contains_commit(self, test_entry):
        """The URL must embed the same commit SHA as the provenance.commit field."""
        _, test = test_entry
        assert (
            test.provenance.commit in test.provenance.url
        ), f"Commit {test.provenance.commit} not found in URL {test.provenance.url}"

    def test_url_file_path_matches_node_id(self, test_entry):
        """The file path in the URL must match the file path in node_id (before ::)."""
        _, test = test_entry
        node_file = test.provenance.node_id.split("::")[0]
        m = URL_PATTERN.match(test.provenance.url)
        assert m, f"URL does not match expected pattern: {test.provenance.url}"
        assert (
            m.group("path") == node_file
        ), f"URL path {m.group('path')!r} != node_id path {node_file!r}"

    def test_url_has_line_range(self, test_entry):
        _, test = test_entry
        m = URL_PATTERN.match(test.provenance.url)
        assert m, f"URL does not match expected pattern: {test.provenance.url}"
        assert m.group("start") and m.group(
            "end"
        ), f"URL missing line range fragment: {test.provenance.url}"
        start, end = int(m.group("start")), int(m.group("end"))
        assert start < end, f"Line range L{start}-L{end} is invalid (start >= end)"

    def test_url_format_is_github_blob(self, test_entry):
        """The URL must be a valid GitHub blob permalink with full SHA."""
        _, test = test_entry
        m = URL_PATTERN.match(test.provenance.url)
        assert (
            m
        ), f"URL does not match expected GitHub blob pattern: {test.provenance.url}"
        assert (
            len(m.group("commit")) == 40
        ), f"Commit in URL should be a full 40-char SHA, got {m.group('commit')!r}"


class TestProvenanceUniqueness:
    def test_no_duplicate_ids(self):
        """Every test ID must be unique across all YAML files."""
        seen = {}
        for yaml_file, test in ALL_TESTS:
            key = test.id
            assert (
                key not in seen
            ), f"Duplicate test ID {key!r}: first in {seen[key]}, also in {yaml_file}"
            seen[key] = yaml_file

    def test_no_duplicate_names(self):
        """Every test name must be unique across all YAML files."""
        seen = {}
        for yaml_file, test in ALL_TESTS:
            key = test.name
            assert key not in seen, (
                f"Duplicate test name {key!r}: "
                f"first in {seen[key]}, also in {yaml_file}"
            )
            seen[key] = yaml_file


# ---------------------------------------------------------------------------
# Source validation
# ---------------------------------------------------------------------------


class TestProvenanceSource:
    def test_source_file_exists(self, test_entry):
        """The source file at the given commit must be fetchable from GitHub."""
        _, test = test_entry
        filepath = test.provenance.node_id.split("::")[0]
        try:
            source = _fetch_source(test.provenance.commit, filepath)
        except Exception as exc:
            pytest.fail(
                f"Cannot fetch {filepath} at commit {test.provenance.commit}: {exc}"
            )
        assert len(source) > 0

    def test_line_range_matches_function(self, test_entry):
        """The URL line range must match the AST-derived boundaries."""
        _, test = test_entry
        parts = test.provenance.node_id.split("::")
        filepath = parts[0]
        if len(parts) >= 3 and "." not in parts[1] and not parts[2].isdigit():
            func_name = f"{parts[1]}.{parts[2]}"
        else:
            func_name = parts[1]

        func_lines = _get_function_lines(test.provenance.commit, filepath)
        if func_name not in func_lines:
            pytest.fail(
                f"Function {func_name!r} not found in {filepath} "
                f"at commit {test.provenance.commit}"
            )

        expected_start, expected_end = func_lines[func_name]
        m = URL_PATTERN.match(test.provenance.url)
        actual_start, actual_end = int(m.group("start")), int(m.group("end"))  # type: ignore

        assert (actual_start, actual_end) == (expected_start, expected_end), (
            f"Line range mismatch for {func_name}: "
            f"URL has L{actual_start}-L{actual_end}, "
            f"AST says L{expected_start}-L{expected_end}"
        )

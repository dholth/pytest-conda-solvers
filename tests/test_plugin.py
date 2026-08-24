import pytest

from pytest_conda_solvers import solver_tests_path

PLUGIN_ARGS = (
    "-p",
    "no:conda-solvers",
    "-p",
    "pytest_conda_solvers.plugin",
)


def test_bundled_suite_preserves_configured_testpaths(pytester):
    pytester.makeini("""
        [pytest]
        testpaths = configured
    """)
    test_dir = pytester.mkdir("configured")
    test_dir.joinpath("test_local.py").write_text("def test_local():\n    pass\n")

    result = pytester.runpytest(*PLUGIN_ARGS, "--collect-only", "-q")

    assert result.ret == pytest.ExitCode.OK
    output = result.stdout.str()
    assert "configured/test_local.py::test_local" in output
    assert "basic.yaml::solve_1_1::test_solve[solve_1_1]" in output


def test_bundled_suite_can_be_disabled_without_disabling_custom_yaml(pytester):
    pytester.makepyfile("""
        def test_local():
            pass
    """)
    pytester.makefile(
        ".yaml",
        custom="""
            tests:
              - name: local solver test
                id: LOCAL001
                provenance:
                  node_id: tests/test_local.py::test_local
                  commit: "0000000000000000000000000000000000000000"
                  url: https://example.invalid/test
                kind: solve
                input:
                  channels: channel-1
                  specs_to_add: numpy
                output:
                  final_state: []
        """,
    )

    result = pytester.runpytest(
        *PLUGIN_ARGS,
        "--collect-only",
        "-q",
        "--no-bundled-solver-tests",
        ".",
        str(solver_tests_path()),
    )

    assert result.ret == pytest.ExitCode.OK
    output = result.stdout.str()
    assert (
        "test_bundled_suite_can_be_disabled_without_disabling_custom_yaml.py::test_local"
        in output
    )
    assert "custom.yaml::local_solver_test::test_solve[local_solver_test]" in output
    assert "basic.yaml::solve_1_1::test_solve[solve_1_1]" not in output


def test_unrelated_yaml_is_ignored(pytester):
    pytester.makepyfile("""
        def test_local():
            pass
    """)
    pytester.makefile(
        ".yaml",
        environment="""
            name: example
            dependencies:
              - python
        """,
    )

    result = pytester.runpytest(
        *PLUGIN_ARGS, "--collect-only", "-q", "--no-bundled-solver-tests"
    )

    assert result.ret == pytest.ExitCode.OK
    output = result.stdout.str()
    assert "test_unrelated_yaml_is_ignored.py::test_local" in output
    assert "environment.yaml" not in output


def test_invalid_solver_yaml_still_reports_a_collection_error(pytester):
    pytester.makefile(
        ".yaml",
        invalid="""
            tests:
              - name: incomplete solver test
        """,
    )

    result = pytester.runpytest(
        *PLUGIN_ARGS, "--collect-only", "-q", "--no-bundled-solver-tests"
    )

    assert result.ret == pytest.ExitCode.INTERRUPTED
    result.stdout.fnmatch_lines(["*invalid.yaml*Object missing required field*"])

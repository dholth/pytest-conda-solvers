from collections.abc import Iterable
from importlib import util
from typing import Any

import msgspec
import pytest
from conda.gateways.logging import initialize_logging
from pytest import (
    Collector,
    Config,
    Item,
    Metafunc,
    Parser,
    PytestPluginManager,
    Session,
)

from .models import TestModule
from .paths import solver_tests_path

initialize_logging()

pytest_plugins = "pytest_conda_solvers.fixtures"


def pytest_addoption(parser: Parser, pluginmanager: PytestPluginManager) -> None:
    group = parser.getgroup("conda_solver")
    group.addoption("--conda-solver", default="libmamba")
    group.addoption(
        "--no-bundled-solver-tests",
        action="store_true",
        help="Do not collect the bundled conda solver test suite.",
    )


def pytest_configure(config: Config) -> None:
    config.addinivalue_line(
        "markers", "conda_solver_test: marks the test for parametrization"
    )


def pytest_sessionstart(session: Session) -> None:
    """Add the bundled suite after pytest resolves its normal collection paths."""
    if session.config.getoption("no_bundled_solver_tests"):
        return

    tests_path = str(solver_tests_path())
    if tests_path not in session.config.args:
        session.config.args.append(tests_path)


def pytest_collect_file(parent, file_path):
    if file_path.suffix != ".yaml":
        return None
    if parent.config.getoption("no_bundled_solver_tests") and (
        file_path.resolve().is_relative_to(solver_tests_path().resolve())
    ):
        return None
    if _is_solver_test_file(file_path):
        return CondaSolverYamlFile.from_parent(parent, path=file_path)


def _is_solver_test_file(file_path) -> bool:
    try:
        data = msgspec.yaml.decode(file_path.read_bytes())
    except msgspec.DecodeError:
        return False
    return isinstance(data, dict) and isinstance(data.get("tests"), list)


def pytest_generate_tests(metafunc: Metafunc) -> None:
    is_conda_solver_test = metafunc.definition.get_closest_marker("conda_solver_test")
    if is_conda_solver_test:
        test_entry = metafunc.definition.parent.parent.test_entry
        if test_entry.test_function == metafunc.definition.name:
            ids = (test_entry.name.replace(" ", "_"),)
            solver = metafunc.config.option.conda_solver
            allowed = test_entry.solvers
            allowed = [allowed] if isinstance(allowed, str) else allowed
            if allowed is not None and solver not in allowed:
                params = (
                    pytest.param(
                        test_entry,
                        marks=pytest.mark.skip(
                            reason=f"only applicable to solvers: {', '.join(allowed)}"
                        ),
                    ),
                )
                metafunc.parametrize("test", params, ids=ids)
                return
            xfail = test_entry.xfail_solvers
            xfail = [xfail] if isinstance(xfail, str) else (xfail or [])
            if solver in xfail:
                params = (
                    pytest.param(
                        test_entry,
                        marks=pytest.mark.xfail(
                            strict=True,
                            reason=test_entry.xfail_reason
                            or f"expected to fail with the {solver} solver",
                        ),
                    ),
                )
                metafunc.parametrize("test", params, ids=ids)
            else:
                metafunc.parametrize("test", (test_entry,), ids=ids)


def pytest_collection_modifyitems(
    session: Session, config: Config, items: list[Item]
) -> None:
    remaining = []
    deselected = []
    for colitem in items:
        cst = colitem.get_closest_marker("conda_solver_test")
        if cst:
            if colitem.name == colitem.originalname:
                deselected.append(colitem)
            else:
                remaining.append(colitem)
                # original_id = item._nodeid
                # base_id, detail_id = original_id.rsplit("::", 1)
                # item._nodeid = base_id  # .replace(".yaml", ".py")
        else:
            remaining.append(colitem)

    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = remaining


class CondaSolverYamlFile(pytest.File):
    def collect(self):
        yield from self._collect_path()

    def _collect_path(self):
        data = self.path.open(encoding="utf-8").read()
        decoded_data = msgspec.yaml.decode(data, type=TestModule)
        for item in decoded_data.tests:
            module = load_module()
            yield CondaSolverTestFile.from_parent(
                self,
                path=self.path,
                obj=module,
                test_entry=item,
                name=f"{item.name}-file",
            )


def load_module():
    module_name = "pytest_conda_solvers.base_tests.install"
    spec = util.find_spec(module_name)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CondaSolverTestFile(pytest.Module):
    def __init__(self, obj: Any, test_entry: Any, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.obj = obj
        self.test_entry = test_entry

    def collect(self) -> Iterable[Item | Collector]:
        """
        Collects a single NutsTestClass instance from this NutsTestFile.
        At the start inject setup_module fixture and parse all fixtures from the module.
        This is directly adopted from pytest.Module.
        """

        self._register_setup_module_fixture()
        self._register_setup_function_fixture()
        self.session._fixturemanager.parsefactories(self)

        name = self.test_entry.name.replace(" ", "_")

        yield CondaSolverTestClass.from_parent(
            self,
            name=name,
            class_name="TestBasic",
        )


class CondaSolverTestClass(pytest.Class):
    def __init__(
        self, parent: CondaSolverTestFile, name: str, class_name: str, **kw: Any
    ):
        super().__init__(name, parent=parent)
        self.params: Any = kw
        self.name: str = name
        self.class_name: str = class_name

    def _getobj(self) -> Any:
        """
        Get the underlying Python object.
        Overwritten from PyobjMixin to separate name and classname.
        This allows to group multiple tests of the same class with
        different parameters to be grouped separately.
        """
        # cf. https://github.com/pytest-dev/pytest/blob/master/src/_pytest/python.py
        assert self.parent is not None
        obj = self.parent.obj  # type: ignore[attr-defined]
        return getattr(obj, self.class_name)

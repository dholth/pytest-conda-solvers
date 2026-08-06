import re
import sys
from contextlib import contextmanager, nullcontext
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch
import pytest
from boltons.setutils import IndexedSet
from conda.base.context import conda_tests_ctxt_mgmt_def_pol
from conda.common.io import env_vars
from conda.core.prefix_data import PrefixData
from conda.core.subdir_data import SubdirData
from conda.exceptions import (
    PackagesNotFoundError,
    ResolvePackageNotFound,
    SpecsConfigurationConflictError,
    UnsatisfiableError,
)
from conda.history import History
from conda.models.channel import Channel
from conda.models.records import PackageRecord, PrefixRecord
from conda.plugins.virtual_packages import cuda
from conda.models.match_spec import MatchSpec

from ..data import get_channel_repodata
from ..models import (
    PackagesNotFoundTestError,
    ResolvePackageNotFoundTestError,
    SpecsConfigurationConflictTestError,
    TestInput,
    UnsatisfiableTestError,
)

EXCEPTION_MAPPING = {
    PackagesNotFoundTestError: PackagesNotFoundError,
    ResolvePackageNotFoundTestError: ResolvePackageNotFound,
    SpecsConfigurationConflictTestError: SpecsConfigurationConflictError,
    UnsatisfiableTestError: UnsatisfiableError,
}


@contextmanager
def get_solver(
    solver_backend,
    tmpdir,
    channel_server,
    channels,
    subdirs,
    specs_to_add=(),
    specs_to_remove=(),
    prefix_records=(),
    history_specs=(),
    add_pip=False,
):
    channels = [
        Channel(channel_server.get_channel_url(channel_name))
        for channel_name in channels
    ]
    tmpdir = tmpdir.strpath
    pd = PrefixData(tmpdir)
    pd._PrefixData__prefix_records = {
        rec.name: PrefixRecord.from_objects(rec) for rec in prefix_records
    }
    spec_map = {spec.name: spec for spec in history_specs}
    with (
        patch.object(History, "get_requested_specs_map", return_value=spec_map),
        env_vars(
            {"CONDA_ADD_PIP_AS_PYTHON_DEPENDENCY": str(add_pip).lower()},
            stack_callback=conda_tests_ctxt_mgmt_def_pol,
        ),
    ):
        if add_pip:
            SubdirData._cache_.clear()
        try:
            yield solver_backend(
                tmpdir,
                channels,
                subdirs,
                specs_to_add=specs_to_add,
                specs_to_remove=specs_to_remove,
            )
        finally:
            if add_pip:
                SubdirData._cache_.clear()


def convert_to_dist_str(state: IndexedSet[PackageRecord]) -> IndexedSet[str]:
    return IndexedSet(prec.dist_str() for prec in state)


def ensure_str_tuple(entry):
    if entry is None:
        return ()
    if isinstance(entry, str):
        return (entry,)
    if isinstance(entry, (list, tuple)):
        return tuple(str(e) for e in entry)
    return (str(entry),)


def ensure_tuple(entry):
    if entry is None:
        return ()
    if isinstance(entry, list):
        return tuple(entry)
    return (entry,)


def add_base_url(base_url, arch, dist_strs):
    return type(dist_strs)(
        f"{base_url}/{dist_str.replace('${{ arch }}', arch)}" for dist_str in dist_strs
    )

# TODO: maybe we should have this in __init__.py instead
@lru_cache(maxsize=None)
def _load_channel_package_index(channel_name, subdir):
    """Load package metadata from channel repodata JSON files."""
    source = "noarch" if subdir == "noarch" else "non-noarch"
    try:
        return load_data_file(Path(f"{channel_name}_{source}.json"))
    except (FileNotFoundError, OSError):
        return {}


def _load_channel_package_index(channel_name, subdir):
    """Load package metadata from channel repodata JSON files."""
    try:
        repodata = get_channel_repodata(channel_name, subdir, "repodata.json")
        return repodata["packages"]
    except (FileNotFoundError, OSError):
        return {}


def package_record_from_dist_str(dist_str):
    DIST_STR_RE = re.compile(
        "(?P<channel>.*)/(?P<subdir>.*)::(?P<name>.*)-(?P<version>.*)-(?P<build>.*?_?(?P<build_number>[0-9]+)?)"
    )
    spec = DIST_STR_RE.fullmatch(dist_str).groupdict()
    # builds without a numeric tail (like blas-1.0-mkl) have no build number
    spec["build_number"] = int(spec["build_number"] or 0)

    # Extract channel name and subdir before modifying spec["channel"]
    channel_name = spec["channel"].rsplit("/", 1)[-1]
    subdir = spec["subdir"]
    filename = f"{spec['name']}-{spec['version']}-{spec['build']}.tar.bz2"

    # TODO: drop when https://github.com/conda/conda/pull/15934 is merged and released
    # Include the subdir in the channel URL so the resulting Channel object
    # has the correct platform field. Without this, on non-Linux hosts the
    # Channel defaults to the native platform (e.g., osx-arm64), causing
    # conda's _supplement_index_dict_with_prefix to treat the prefix record's
    # channel as mismatched and corrupt its canonical_name with the native
    # platform URL, and that in-turn produces weird wrong dist-strings like
    # "channel-1/osx-arm64/linux-64::pkg" instead of "channel-1/linux-64::pkg".
    spec["channel"] = f"{spec['channel']}/{spec['subdir']}"

    # Inject depends from channel repodata so solvers can correctly determine
    # which packages need updating when update modifiers are applied.
    index = _load_channel_package_index(channel_name, subdir)
    pkg_meta = index.get(filename, {})
    spec["depends"] = pkg_meta.get("depends", [])

    return PackageRecord.from_objects(**spec)


def prepare_solver_input(raw_solver_input: TestInput, channel_server, arch):
    def get_env_pair(raw_solver_input, name, join_str=None):
        var_name = f"CONDA_{name.upper()}"
        val = getattr(raw_solver_input, name)
        if isinstance(val, list):
            val = join_str.join(val)
        return var_name, str(val) if val is not None else None

    solver_input = {}
    for simple_key in ("channels", "subdirs"):
        solver_input[simple_key] = ensure_str_tuple(
            getattr(raw_solver_input, simple_key)
        )
    solver_input["prefix_records"] = diststrs_to_records(
        raw_solver_input.prefix, channel_server, arch
    )
    for spec_key in ("specs_to_add", "specs_to_remove", "history_specs"):
        solver_input[spec_key] = tuple(
            MatchSpec(s) for s in ensure_str_tuple(getattr(raw_solver_input, spec_key))
        )
    solver_input["add_pip"] = raw_solver_input.add_pip
    env_vars = {
        name: val
        for name, val in (
            get_env_pair(raw_solver_input, "pinned_packages", "&"),
            get_env_pair(raw_solver_input, "aggressive_update_packages", ","),
            get_env_pair(raw_solver_input, "auto_update_conda"),
            get_env_pair(raw_solver_input, "channel_priority"),
            get_env_pair(raw_solver_input, "override_cuda"),
            get_env_pair(raw_solver_input, "override_glibc"),
        )
        if val is not None
    }
    bool_flags = ("ignore_pinned", "force_reinstall", "prune", "force_remove")
    enum_flags = ("update_modifier", "deps_modifier")
    flags = {
        flag: v
        for flag in bool_flags
        if (v := getattr(raw_solver_input, flag)) is not None
    } | {
        flag: v
        for flag in enum_flags
        if (v := getattr(raw_solver_input, flag)) is not None
    }
    return solver_input, env_vars, flags


def diststrs_to_records(diststrs, channel_server, arch):
    return tuple(
        package_record_from_dist_str(dist_str)
        for dist_str in add_base_url(
            channel_server.get_base_url(),
            arch,
            ensure_str_tuple(diststrs),
        )
    )


def prepare_error_information(error):
    exception_class = EXCEPTION_MAPPING[type(error)]
    error_info = {
        "exception": exception_class,
    }
    if exception_class in (
        UnsatisfiableError,
        ResolvePackageNotFound,
        PackagesNotFoundError,
    ):
        error_info["entries"] = set(
            tuple(map(MatchSpec, ensure_tuple(entries))) for entries in error.entries
        )
        assert len(error.entries) == len(error_info["entries"])
        if exception_class == UnsatisfiableError:
            error_info["message_excludes"] = ensure_str_tuple(error.message_excludes)
            error_info["message_includes"] = ensure_str_tuple(error.message_includes)
    elif exception_class == SpecsConfigurationConflictError:
        error_info["requested_specs"] = ensure_str_tuple(error.requested_specs)
        error_info["pinned_specs"] = ensure_str_tuple(error.pinned_specs)
    return error_info


class TestBasic:
    @contextmanager
    def _setup_solver(self, solver_backend, channel_server, tmpdir, test_input):
        solver_input, env, flags = prepare_solver_input(
            test_input,
            channel_server,
            "linux-64",
        )
        with (
            env_vars(
                env,
                stack_callback=conda_tests_ctxt_mgmt_def_pol,
            )
            if len(env) > 0
            else nullcontext()
        ):
            if test_input.set_sys_prefix:
                saved_sys_prefix = sys.prefix
                sys.prefix = tmpdir.strpath
            if "CONDA_OVERRIDE_CUDA" in env:
                cuda.cached_cuda_version.cache_clear()
            try:
                with get_solver(
                    solver_backend,
                    tmpdir,
                    channel_server,
                    **solver_input,
                ) as solver:
                    yield solver, solver_input, env, flags
            finally:
                if test_input.set_sys_prefix:
                    sys.prefix = saved_sys_prefix

    @pytest.mark.conda_solver_test
    def test_solve(self, env, tmpdir, solver_backend, test, channel_server):
        with self._setup_solver(solver_backend, channel_server, tmpdir, test.input) as (
            solver,
            solver_input,
            env,
            flags,
        ):
            final_state = solver.solve_final_state(**flags)

        if test.output.final_state is None:
            # must-solve mode: upstream only requires that the solve succeeds
            return
        ref = add_base_url(
            channel_server.get_base_url(), "linux-64", test.output.final_state
        )
        assert sorted(list(convert_to_dist_str(final_state))) == sorted(list(ref))
        # list() on both sides: IndexedSet == list would degrade to set equality
        assert list(convert_to_dist_str(final_state)) == list(ref)

    @pytest.mark.conda_solver_test
    def test_solve_for_diff(self, env, tmpdir, solver_backend, test, channel_server):
        with self._setup_solver(
            solver_backend,
            channel_server,
            tmpdir,
            test.input,
        ) as (
            solver,
            solver_input,
            env,
            flags,
        ):
            unlink_precs, link_precs = solver.solve_for_diff(**flags)

        unlink_ref = add_base_url(
            channel_server.get_base_url(), "linux-64", test.output.unlink_precs
        )
        link_ref = add_base_url(
            channel_server.get_base_url(), "linux-64", test.output.link_precs
        )
        assert sorted(list(convert_to_dist_str(unlink_precs))) == sorted(
            list(unlink_ref)
        )
        assert list(convert_to_dist_str(unlink_precs)) == list(unlink_ref)
        assert sorted(list(convert_to_dist_str(link_precs))) == sorted(list(link_ref))
        assert list(convert_to_dist_str(link_precs)) == list(link_ref)

    @pytest.mark.conda_solver_test
    def test_determine_constricting_specs(
        self, env, tmpdir, solver_backend, test, channel_server
    ):
        solution_precs = [
            PrefixRecord.from_objects(r) for r in test.input.solution_records
        ]
        with self._setup_solver(
            solver_backend,
            channel_server,
            tmpdir,
            test.input,
        ) as (
            solver,
            solver_input,
            env,
            flags,
        ):
            constrictions = solver.determine_constricting_specs(
                solver_input["specs_to_add"][0], solution_precs
            )

        assert constrictions == test.output.constrictions_as_list()

    @pytest.mark.conda_solver_test
    def test_unsatisfiable(self, env, tmpdir, solver_backend, test, channel_server):
        error_info = prepare_error_information(test.error)
        with (
            self._setup_solver(solver_backend, channel_server, tmpdir, test.input) as (
                solver,
                solver_input,
                env,
                flags,
            ),
            pytest.raises(
                (
                    UnsatisfiableError,
                    PackagesNotFoundError,
                    ResolvePackageNotFound,
                    SpecsConfigurationConflictError,
                )
            ) as exc_info,
        ):
            if test.operation == "solve_for_diff":
                solver.solve_for_diff(**flags)
            else:
                solver.solve_final_state(**flags)

        assert isinstance(exc_info.value, error_info["exception"]), (
            f"Expected {error_info['exception'].__name__}, "
            f"got {type(exc_info.value).__name__}"
        )

        match exc_info.value:
            case UnsatisfiableError() as exc:
                unsatisfiable = getattr(exc, "unsatisfiable", None)
                if error_info.get("entries"):
                    if unsatisfiable is not None:
                        # classic solver branch
                        assert set(unsatisfiable) == set(error_info["entries"])
                    else:
                        # LibMambaUnsatisfiableError: here we verify that the endpoint
                        # packages of each conflict chain appear in the message (and
                        # intermediaries _may_ be omitted in some scenarios, like B006)
                        message = str(exc)
                        expected_names = set()
                        for entry_tuple in error_info["entries"]:
                            expected_names.add(entry_tuple[0].name)
                            expected_names.add(entry_tuple[-1].name)
                        for name in expected_names:
                            assert name in message, (
                                f"Expected conflicting package {name!r} "
                                f"not mentioned in error message"
                            )
            case ResolvePackageNotFound() as exc:
                # classic solver only. bad_deps is a flat tuple of MatchSpecs, wrapped
                # here to match the set-of-tuples structure in error_info["entries"]
                if error_info.get("entries"):
                    assert set((exc.bad_deps,)) == set(error_info["entries"])
            case PackagesNotFoundError() as exc:
                if error_info.get("entries"):
                    expected_names = {
                        spec.name
                        for entry_tuple in error_info["entries"]
                        for spec in entry_tuple
                    }
                    actual_names = {package.name for package in exc.packages}
                    # only compare package names, not full MatchSpecs. the YAML
                    # entries use classic solver syntax, but libmamba constructs
                    # its MatchSpecs from error-message parsing with different
                    # version constraint formatting (say, B007, with '1.5,=1.6.*',
                    # vs '1.5.*,1.6.*')
                    assert actual_names == expected_names
            case SpecsConfigurationConflictError() as exc:
                kwargs = exc._kwargs
                assert set(kwargs["requested_specs"]) == set(error_info["requested_specs"])
                assert set(kwargs["pinned_specs"]) == set(error_info["pinned_specs"])

        for fragment in error_info.get("message_excludes", ()):
            assert fragment not in str(exc_info.value), (
                f"Fragment {fragment!r} must not appear in the error message"
            )
        for fragment in error_info.get("message_includes", ()):
            assert fragment in str(exc_info.value), (
                f"Fragment {fragment!r} must appear in the error message"
            )

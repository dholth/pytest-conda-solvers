# Copyright (C) 2012 Anaconda, Inc
# SPDX-License-Identifier: BSD-3-Clause
"""White-box solver tests that cannot be expressed as YAML specs."""

import copy
from unittest.mock import Mock

import pytest
from conda.core.solve import UpdateModifier
from conda.models.channel import Channel
from conda.models.match_spec import MatchSpec
from conda.models.records import PrefixRecord

from .install import (
    add_base_url,
    diststrs_to_records,
    get_solver,
)


class TestSolveRegressions:
    def test_solve_2_ssc_add_back(self, request, tmpdir, solver_backend, channel_server):
        """Regression test: after _run_sat, orphaned duplicate records in the
        SolverStateContainer's solution must be deduplicated by name.

        Provenance: tests/core/test_solve.py::test_solve_2 (stages 1-3)
        at conda commit 03329e0f4a627c9b9aa92ef34f7f93b9aa83e438,
        https://github.com/conda/conda/blob/03329e0f4a627c9b9aa92ef34f7f93b9aa83e438/tests/core/test_solve.py#L127-L203
        Stage 1 runs here as well, because stage 2 consumes its final_state
        as the prefix (the chained-handoff convention), and its order
        assertion re-runs what B112 in conda-solver-tests/basic.yaml already
        covers.
        """
        if request.config.option.conda_solver != "classic":
            pytest.skip(
                "conda-libmamba-solver does not use Solver.ssc (SolverStateContainer)"
            )

        channels = ("channel-2", "channel-4")
        subdirs = ("linux-64", "noarch")
        specs = (MatchSpec("numpy"),)

        with get_solver(
            solver_backend,
            tmpdir,
            channel_server,
            channels,
            subdirs,
            specs_to_add=specs,
        ) as solver:
            final_state = solver.solve_final_state()
            order = add_base_url(
                channel_server.get_base_url(),
                "linux-64",
                (
                    "channel-2/${{ arch }}::mkl-2017.0.3-0",
                    "channel-2/${{ arch }}::openssl-1.0.2l-0",
                    "channel-2/${{ arch }}::readline-6.2-2",
                    "channel-2/${{ arch }}::sqlite-3.13.0-0",
                    "channel-2/${{ arch }}::tk-8.5.18-0",
                    "channel-2/${{ arch }}::xz-5.2.3-0",
                    "channel-2/${{ arch }}::zlib-1.2.11-0",
                    "channel-2/${{ arch }}::python-3.6.2-0",
                    "channel-2/${{ arch }}::numpy-1.13.1-py36_0",
                ),
            )
            # upstream's convert_to_dist_str returns a tuple, so duplicate
            # records must not collapse. The harness helper returns an
            # IndexedSet, which would dedupe, so build the list directly.
            assert [prec.dist_str() for prec in final_state] == list(order)

        # upstream: MatchSpec("channel-4::numpy"). The bare channel name would
        # resolve against the default channel alias, so point it at the served
        # channel-4 URL instead.
        specs_to_add = (
            MatchSpec(
                "numpy", channel=channel_server.get_channel_url("channel-4")
            ),
        )
        with get_solver(
            solver_backend,
            tmpdir,
            channel_server,
            channels,
            subdirs,
            specs_to_add=specs_to_add,
            prefix_records=final_state,
            history_specs=specs,
        ) as solver:
            solver.solve_final_state()
            extra_prec = PrefixRecord(
                _hash=5842798532132402024,
                name="mkl",
                version="2017.0.3",
                build="0",
                build_number=0,
                channel=Channel("channel-2/osx-64"),
                subdir="osx-64",
                fn="mkl-2017.0.3-0.tar.bz2",
                md5="76cfa5d21e73db338ffccdbe0af8a727",
                url="https://conda.anaconda.org/channel-2/osx-64/mkl-2017.0.3-0.tar.bz2",
                arch="x86_64",
                platform="darwin",
                depends=(),
                constrains=(),
                track_features=(),
                features=(),
                license="proprietary - Intel",
                license_family="Proprietary",
                timestamp=0,
                date="2017-06-26",
                size=135839394,
            )

            solver_ssc = copy.copy(solver.ssc)
            ssc = solver.ssc
            ssc.add_back_map = [MatchSpec("mkl")]
            # Do a transformation from set -> list for precs (done in _run_sat)
            sol_precs = [_ for _ in ssc.solution_precs]
            ssc.solution_precs = copy.copy(sol_precs)
            # Add extra prec to the ssc being used
            sol_precs.append(extra_prec)
            solver_ssc.solution_precs = sol_precs

            # Last modification to ssc before finding orphaned packages is done
            # by run_sat
            solver._run_sat = Mock(return_value=ssc)
            # Give solver the modified ssc
            solver.ssc = solver_ssc
            final_state = solver.solve_final_state(
                update_modifier=UpdateModifier.UPDATE_ALL
            )
            prec_names = [_.name for _ in final_state]
            assert len(prec_names) == len(set(prec_names))

    def test_force_reinstall_1_sequence(self, tmpdir, solver_backend, channel_server):
        """Calls 2-4 of test_force_reinstall_1 on one solver instance: an
        ordinary diff is empty, force_reinstall relinks exactly one record
        (python), and the next ordinary call on the same instance is empty
        again, proving the forced call leaves no state behind.

        Provenance: tests/core/test_solve.py::test_force_reinstall_1 (calls 2-4)
        at conda commit 03329e0f4a627c9b9aa92ef34f7f93b9aa83e438,
        https://github.com/conda/conda/blob/03329e0f4a627c9b9aa92ef34f7f93b9aa83e438/tests/core/test_solve.py#L2681-L2714
        Stage 1 is ported as B162, and stages 2-3 as B048/B049, in
        conda-solver-tests/basic.yaml.
        """
        channels = ("channel-1",)
        subdirs = ("linux-64", "noarch")
        specs = (MatchSpec("python=2"),)
        prefix_records = diststrs_to_records(
            (
                "channel-1/${{ arch }}::openssl-1.0.1c-0",
                "channel-1/${{ arch }}::readline-6.2-0",
                "channel-1/${{ arch }}::sqlite-3.7.13-0",
                "channel-1/${{ arch }}::system-5.8-1",
                "channel-1/${{ arch }}::tk-8.5.13-0",
                "channel-1/${{ arch }}::zlib-1.2.7-0",
                "channel-1/${{ arch }}::python-2.7.5-0",
            ),
            channel_server,
            "linux-64",
        )
        with get_solver(
            solver_backend,
            tmpdir,
            channel_server,
            channels,
            subdirs,
            specs_to_add=specs,
            prefix_records=prefix_records,
            history_specs=specs,
        ) as solver:
            unlink_dists, link_dists = solver.solve_for_diff()
            assert not unlink_dists
            assert not link_dists

            unlink_dists, link_dists = solver.solve_for_diff(force_reinstall=True)
            assert len(unlink_dists) == len(link_dists) == 1
            assert unlink_dists[0] == link_dists[0]

            unlink_dists, link_dists = solver.solve_for_diff()
            assert not unlink_dists
            assert not link_dists

    def test_globstr_matchspec_non_compatible_construction(
        self, tmpdir, solver_backend, channel_server
    ):
        """Case 1 of test_globstr_matchspec_non_compatible: directly
        incompatible build-string globs raise ValueError during solver
        construction, before any solve starts.

        Provenance: tests/core/test_solve.py::test_globstr_matchspec_non_compatible
        (case 1) at conda commit 03329e0f4a627c9b9aa92ef34f7f93b9aa83e438,
        https://github.com/conda/conda/blob/03329e0f4a627c9b9aa92ef34f7f93b9aa83e438/tests/core/test_solve.py#L3833-L3842
        Cases 2-3 are ported as B083/B084 in conda-solver-tests/basic.yaml.
        """
        specs = (MatchSpec("accelerate=*=np17*"), MatchSpec("accelerate=*=np16*"))
        with pytest.raises(ValueError):
            with get_solver(
                solver_backend,
                tmpdir,
                channel_server,
                ("channel-1",),
                ("linux-64", "noarch"),
                specs_to_add=specs,
            ) as solver:
                solver.solve_final_state()

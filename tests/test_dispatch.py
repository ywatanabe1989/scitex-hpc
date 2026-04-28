"""Tests for scitex_hpc.srun / sbatch dispatch (mocked scitex_ssh)."""

from __future__ import annotations

from unittest import mock

import pytest

from scitex_hpc import JobConfig, sbatch, srun
from scitex_hpc._dispatch import _quote


def _result(returncode=0, stdout="", stderr=""):
    return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)


def test_srun_raises_without_command() -> None:
    cfg = JobConfig(project="x")
    with pytest.raises(ValueError, match="command is required"):
        srun(cfg)


def test_srun_invokes_ssh_with_login_shell_wrapped_command() -> None:
    # Explicit host + remote_base so the test is independent of the CI/dev
    # environment's user-config defaults.
    cfg = JobConfig(
        project="demo",
        command="echo hi",
        cpus=4,
        host="spartan",
        remote_base="~/proj",
    )
    with mock.patch("scitex_hpc._dispatch.exec_remote") as run:
        run.return_value = _result(returncode=0)
        rc = srun(cfg)
    assert rc == 0
    # exec_remote(host, command, ...) — positional args
    call_args, _call_kwargs = run.call_args
    host = call_args[0]
    remote_cmd = call_args[1]
    assert host == "spartan"
    # The wrapper must run via login shell so srun is on PATH.
    assert "bash -lc" in remote_cmd
    # The remote command must invoke srun with the resolved cpus.
    assert "srun" in remote_cmd
    assert "--cpus-per-task=4" in remote_cmd
    # The remote command must cd to remote_base/project before launching.
    assert "cd ~/proj/demo" in remote_cmd


def test_sbatch_returns_job_id_on_success() -> None:
    cfg = JobConfig(project="demo", command="echo hi")
    with mock.patch("scitex_hpc._dispatch.exec_remote") as run:
        run.return_value = _result(
            returncode=0,
            stdout="Submitted batch job 24386489\n",
            stderr="",
        )
        job_id = sbatch(cfg)
    assert job_id == "24386489"


def test_sbatch_returns_none_on_failure() -> None:
    cfg = JobConfig(project="demo", command="echo hi")
    with mock.patch("scitex_hpc._dispatch.exec_remote") as run:
        run.return_value = _result(returncode=1, stdout="", stderr="oops")
        assert sbatch(cfg) is None


def test_sbatch_returns_none_when_stdout_lacks_job_id() -> None:
    cfg = JobConfig(project="demo", command="echo hi")
    with mock.patch("scitex_hpc._dispatch.exec_remote") as run:
        run.return_value = _result(returncode=0, stdout="weird stdout", stderr="")
        assert sbatch(cfg) is None


def test_quote_escapes_single_quotes() -> None:
    assert _quote("a 'b' c") == "'a '\\''b'\\'' c'"

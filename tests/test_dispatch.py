"""Tests for scitex_hpc.srun / sbatch dispatch (mocked subprocess)."""

from __future__ import annotations

from unittest import mock

import pytest

from scitex_hpc import JobConfig, sbatch, srun
from scitex_hpc._dispatch import _quote


def test_srun_raises_without_command() -> None:
    cfg = JobConfig(project="x")
    with pytest.raises(ValueError, match="command is required"):
        srun(cfg)


def test_srun_invokes_ssh_with_login_shell_wrapped_command() -> None:
    cfg = JobConfig(project="demo", command="echo hi", cpus=4)
    with mock.patch("scitex_hpc._dispatch.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0)
        rc = srun(cfg)
    assert rc == 0
    args = run.call_args[0][0]
    assert args[0] == "ssh"
    # The wrapper must run via login shell so srun is on PATH.
    assert "bash -lc" in args[2]
    # The remote command must invoke srun with the resolved cpus.
    assert "srun" in args[2]
    assert "--cpus-per-task=4" in args[2]
    # The remote command must cd to remote_base/project before launching.
    assert "cd ~/proj/demo" in args[2]


def test_sbatch_returns_job_id_on_success() -> None:
    cfg = JobConfig(project="demo", command="echo hi")
    with mock.patch("scitex_hpc._dispatch.subprocess.run") as run:
        run.return_value = mock.Mock(
            returncode=0,
            stdout="Submitted batch job 24386489\n",
            stderr="",
        )
        job_id = sbatch(cfg)
    assert job_id == "24386489"


def test_sbatch_returns_none_on_failure() -> None:
    cfg = JobConfig(project="demo", command="echo hi")
    with mock.patch("scitex_hpc._dispatch.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=1, stdout="", stderr="oops")
        assert sbatch(cfg) is None


def test_sbatch_returns_none_when_stdout_lacks_job_id() -> None:
    cfg = JobConfig(project="demo", command="echo hi")
    with mock.patch("scitex_hpc._dispatch.subprocess.run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="weird stdout", stderr="")
        assert sbatch(cfg) is None


def test_quote_escapes_single_quotes() -> None:
    assert _quote("a 'b' c") == "'a '\\''b'\\'' c'"

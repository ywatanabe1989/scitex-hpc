"""Tests for scitex_hpc.srun / sbatch dispatch.

Uses the ``runner=`` DI seam on ``srun`` / ``sbatch`` with a hand-rolled
fake — no ``unittest.mock``, no ``monkeypatch``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from scitex_hpc import JobConfig, sbatch, srun
from scitex_hpc._dispatch import _quote

# ---------------------------------------------------------------------------
# Hand-rolled fakes
# ---------------------------------------------------------------------------


@dataclass
class _Result:
    """Hand-rolled CompletedProcess / SSHResult shape."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class _FakeRunner:
    """Records each (host, command) and returns a scripted result."""

    def __init__(self, *, result: _Result | None = None):
        self.calls: list[tuple[str, str]] = []
        self._result = result if result is not None else _Result()

    def __call__(self, host, command, *, check=False, timeout=None):
        self.calls.append((host, command))
        return self._result


# ---------------------------------------------------------------------------
# srun
# ---------------------------------------------------------------------------


def test_srun_raises_when_command_is_empty():
    # Arrange
    cfg = JobConfig(project="x")
    action = srun

    # Act
    def _call():
        action(cfg)

    # Assert
    with pytest.raises(ValueError, match="command is required"):
        _call()


def test_srun_invokes_runner_with_configured_host():
    # Arrange
    cfg = JobConfig(
        project="demo",
        command="echo hi",
        cpus=4,
        host="spartan",
        remote_base="~/proj",
    )
    runner = _FakeRunner(result=_Result(returncode=0))
    # Act
    srun(cfg, runner=runner)
    # Assert
    assert runner.calls[0][0] == "spartan"


def test_srun_wraps_remote_command_in_login_shell():
    # Arrange
    cfg = JobConfig(
        project="demo",
        command="echo hi",
        cpus=4,
        host="spartan",
        remote_base="~/proj",
    )
    runner = _FakeRunner(result=_Result(returncode=0))
    # Act
    srun(cfg, runner=runner)
    # Assert
    assert "bash -lc" in runner.calls[0][1]


def test_srun_includes_resolved_cpus_in_remote_command():
    # Arrange
    cfg = JobConfig(
        project="demo",
        command="echo hi",
        cpus=4,
        host="spartan",
        remote_base="~/proj",
    )
    runner = _FakeRunner(result=_Result(returncode=0))
    # Act
    srun(cfg, runner=runner)
    # Assert
    assert "--cpus-per-task=4" in runner.calls[0][1]


def test_srun_cds_to_remote_base_project_before_launch():
    # Arrange
    cfg = JobConfig(
        project="demo",
        command="echo hi",
        cpus=4,
        host="spartan",
        remote_base="~/proj",
    )
    runner = _FakeRunner(result=_Result(returncode=0))
    # Act
    srun(cfg, runner=runner)
    # Assert
    assert "cd ~/proj/demo" in runner.calls[0][1]


def test_srun_returns_runner_returncode_on_success():
    # Arrange
    cfg = JobConfig(
        project="demo",
        command="echo hi",
        cpus=4,
        host="spartan",
        remote_base="~/proj",
    )
    runner = _FakeRunner(result=_Result(returncode=0))
    # Act
    rc = srun(cfg, runner=runner)
    # Assert
    assert rc == 0


# ---------------------------------------------------------------------------
# sbatch
# ---------------------------------------------------------------------------


def test_sbatch_returns_parsed_job_id_on_success():
    # Arrange
    cfg = JobConfig(project="demo", command="echo hi")
    runner = _FakeRunner(
        result=_Result(returncode=0, stdout="Submitted batch job 24386489\n")
    )
    # Act
    job_id = sbatch(cfg, runner=runner)
    # Assert
    assert job_id == "24386489"


def test_sbatch_returns_none_when_runner_returncode_nonzero():
    # Arrange
    cfg = JobConfig(project="demo", command="echo hi")
    runner = _FakeRunner(result=_Result(returncode=1, stdout="", stderr="oops"))
    # Act
    job_id = sbatch(cfg, runner=runner)
    # Assert
    assert job_id is None


def test_sbatch_returns_none_when_stdout_has_no_job_id():
    # Arrange
    cfg = JobConfig(project="demo", command="echo hi")
    runner = _FakeRunner(result=_Result(returncode=0, stdout="weird stdout"))
    # Act
    job_id = sbatch(cfg, runner=runner)
    # Assert
    assert job_id is None


# ---------------------------------------------------------------------------
# _quote
# ---------------------------------------------------------------------------


def test_quote_escapes_single_quotes_in_input():
    # Arrange
    raw = "a 'b' c"
    # Act
    quoted = _quote(raw)
    # Assert
    assert quoted == "'a '\\''b'\\'' c'"

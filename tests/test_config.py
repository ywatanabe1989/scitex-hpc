"""Tests for scitex_hpc.JobConfig."""

from __future__ import annotations

import pytest

from scitex_hpc import HPC_DEFAULTS, JobConfig


def test_jobconfig_resolve_uses_explicit_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCITEX_HPC_PARTITION", "from-env")
    cfg = JobConfig(project="x", partition="from-explicit")
    assert cfg.resolve("partition") == "from-explicit"


def test_jobconfig_resolve_uses_env_when_no_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCITEX_HPC_PARTITION", "from-env")
    cfg = JobConfig(project="x")
    assert cfg.resolve("partition") == "from-env"


def test_jobconfig_resolve_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCITEX_HPC_PARTITION", raising=False)
    cfg = JobConfig(project="x")
    assert cfg.resolve("partition") == HPC_DEFAULTS["partition"]


def test_slurm_args_includes_required_flags() -> None:
    cfg = JobConfig(
        project="demo", cpus=8, time="00:05:00", mem="4G", partition="cascade"
    )
    args = cfg.slurm_args()
    assert "--partition=cascade" in args
    assert "--cpus-per-task=8" in args
    assert "--time=00:05:00" in args
    assert "--mem=4G" in args
    assert any(a.startswith("--job-name=scitex-demo") for a in args)


def test_slurm_args_uses_explicit_job_name() -> None:
    cfg = JobConfig(project="demo", job_name="my-special-job")
    args = cfg.slurm_args()
    assert "--job-name=my-special-job" in args


def test_jobconfig_int_field_returns_string() -> None:
    """resolve() always returns str even when the field is an int (cpus)."""
    cfg = JobConfig(project="x", cpus=32)
    assert cfg.resolve("cpus") == "32"

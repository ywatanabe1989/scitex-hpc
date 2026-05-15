"""Tests for scitex_hpc.JobConfig.

Real env-var mutation in yield-fixtures + a hand-rolled
``user_defaults_loader`` injected via the production seam. No
``monkeypatch``, no mocks.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from scitex_hpc import HPC_DEFAULTS, JobConfig

# ---------------------------------------------------------------------------
# Fixtures (real env-var mutation, yield-based teardown — no monkeypatch)
# ---------------------------------------------------------------------------


@pytest.fixture
def _clean_partition_env():
    """Save / restore ``SCITEX_HPC_PARTITION`` around each test."""
    # Arrange
    key = "SCITEX_HPC_PARTITION"
    prior = os.environ.get(key)
    os.environ.pop(key, None)
    try:
        # Act / Assert — yield to the test body.
        yield
    finally:
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior


def _empty_user_defaults() -> dict[str, Any]:
    """Real callable: pretend ``~/.scitex/*`` is empty. Used in tests
    that need to verify env / default fallback without bleed-through
    from the developer's actual user-config files."""
    return {}


def _user_defaults_with_partition(value: str):
    """Build a real callable returning a populated user-config dict."""

    def _loader() -> dict[str, Any]:
        return {"partition": value}

    return _loader


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------


def test_jobconfig_resolve_returns_explicit_value_first(_clean_partition_env):
    # Arrange
    os.environ["SCITEX_HPC_PARTITION"] = "from-env"
    cfg = JobConfig(project="x", partition="from-explicit")
    # Act
    resolved = cfg.resolve("partition", user_defaults_loader=_empty_user_defaults)
    # Assert
    assert resolved == "from-explicit"


def test_jobconfig_resolve_returns_env_when_no_explicit(_clean_partition_env):
    # Arrange
    os.environ["SCITEX_HPC_PARTITION"] = "from-env"
    cfg = JobConfig(project="x")
    # Act
    resolved = cfg.resolve("partition", user_defaults_loader=_empty_user_defaults)
    # Assert
    assert resolved == "from-env"


def test_jobconfig_resolve_returns_user_config_above_default(_clean_partition_env):
    # Arrange
    cfg = JobConfig(project="x")
    loader = _user_defaults_with_partition("from-user-cfg")
    # Act
    resolved = cfg.resolve("partition", user_defaults_loader=loader)
    # Assert
    assert resolved == "from-user-cfg"


def test_jobconfig_resolve_falls_back_to_builtin_default(_clean_partition_env):
    # Arrange
    cfg = JobConfig(project="x")
    # Act
    resolved = cfg.resolve("partition", user_defaults_loader=_empty_user_defaults)
    # Assert
    assert resolved == HPC_DEFAULTS["partition"]


# ---------------------------------------------------------------------------
# slurm_args()
# ---------------------------------------------------------------------------


def test_slurm_args_includes_resolved_partition_flag():
    # Arrange
    cfg = JobConfig(
        project="demo", cpus=8, time="00:05:00", mem="4G", partition="cascade"
    )
    # Act
    args = cfg.slurm_args()
    # Assert
    assert "--partition=cascade" in args


def test_slurm_args_includes_resolved_cpus_flag():
    # Arrange
    cfg = JobConfig(
        project="demo", cpus=8, time="00:05:00", mem="4G", partition="cascade"
    )
    # Act
    args = cfg.slurm_args()
    # Assert
    assert "--cpus-per-task=8" in args


def test_slurm_args_includes_resolved_time_flag():
    # Arrange
    cfg = JobConfig(
        project="demo", cpus=8, time="00:05:00", mem="4G", partition="cascade"
    )
    # Act
    args = cfg.slurm_args()
    # Assert
    assert "--time=00:05:00" in args


def test_slurm_args_includes_resolved_mem_flag():
    # Arrange
    cfg = JobConfig(
        project="demo", cpus=8, time="00:05:00", mem="4G", partition="cascade"
    )
    # Act
    args = cfg.slurm_args()
    # Assert
    assert "--mem=4G" in args


def test_slurm_args_derives_job_name_from_project_when_unset():
    # Arrange
    cfg = JobConfig(
        project="demo", cpus=8, time="00:05:00", mem="4G", partition="cascade"
    )
    # Act
    args = cfg.slurm_args()
    # Assert
    assert any(a.startswith("--job-name=scitex-demo") for a in args)


def test_slurm_args_uses_explicit_job_name_when_supplied():
    # Arrange
    cfg = JobConfig(project="demo", job_name="my-special-job")
    # Act
    args = cfg.slurm_args()
    # Assert
    assert "--job-name=my-special-job" in args


# ---------------------------------------------------------------------------
# Int / str field coercion
# ---------------------------------------------------------------------------


def test_jobconfig_int_field_resolves_as_stringified_value():
    """resolve() always returns str even when the field is an int (cpus)."""
    # Arrange
    cfg = JobConfig(project="x", cpus=32)
    # Act
    resolved = cfg.resolve("cpus")
    # Assert
    assert resolved == "32"

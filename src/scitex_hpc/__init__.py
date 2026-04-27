"""scitex-hpc — generic SLURM dispatch for the SciTeX ecosystem.

Public API:

    from scitex_hpc import (
        JobConfig,
        srun,                # blocking interactive run via srun
        sbatch,              # async batch submission, returns job ID
        sync,                # rsync project to HPC host
        poll_job,            # check sacct status for a job ID
        fetch_result,        # scp the full output of a sbatch job
    )

Default config matches typical SciTeX use (Spartan / sapphire), but every
field is overridable through the `JobConfig` dataclass and via environment
variables prefixed `SCITEX_HPC_*`.

Login nodes never run compute — every command is wrapped in srun/sbatch.
"""

from __future__ import annotations

__version__ = "0.1.0"

from ._config import HPC_DEFAULTS, JobConfig
from ._dispatch import sbatch, srun
from ._results import fetch_result, poll_job
from ._sync import sync

__all__ = [
    "HPC_DEFAULTS",
    "JobConfig",
    "fetch_result",
    "poll_job",
    "sbatch",
    "srun",
    "sync",
]

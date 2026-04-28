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

No cluster names are baked in. Defaults resolve via cascade:
explicit `JobConfig(...)` → `SCITEX_HPC_*` env vars →
`~/.scitex/{hpc,dev}/config.yaml` (`hpc.defaults.*`) → cluster-agnostic
fallbacks. Set your `host` / `partition` once in user config and forget.

Login nodes never run compute — every command is wrapped in srun/sbatch.
"""

from __future__ import annotations

__version__ = "0.6.1"

from ._config import HPC_DEFAULTS, JobConfig
from ._dispatch import sbatch, srun
from ._reservation import Reservation
from ._results import fetch_result, poll_job
from ._sync import sync

__all__ = [
    "HPC_DEFAULTS",
    "JobConfig",
    "Reservation",
    "fetch_result",
    "poll_job",
    "sbatch",
    "srun",
    "sync",
]
